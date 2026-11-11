;;; dynra.el --- Live Python development with Dynra -*- lexical-binding: t; -*-

;; Copyright (C) 2026 Dynra contributors
;; Author: Dynra contributors
;; Keywords: languages, python, tools
;; Package-Requires: ((emacs "27.1"))
;; Version: 0.4.1

;;; Commentary:

;; CIDER-style evaluation and project-aware jack-in for Dynra.
;; Enable `dynra-auto-mode' to discover a running Dynra runtime whenever a
;; Python file opens.  Use `dynra-jack-in' when the project has no runtime.

;;; Code:

(require 'cl-lib)
(require 'comint)
(require 'json)
(require 'project)
(require 'python)
(require 'subr-x)

(defgroup dynra nil
  "Live Python development with Dynra."
  :group 'python
  :prefix "dynra-")

(defcustom dynra-command '("uv" "run" "dynra" "--port" "0")
  "Command used by `dynra-jack-in'.
Dynra must already be installed in the project's environment."
  :type '(repeat string))

(defcustom dynra-auto-connect t
  "Whether opening a Python file connects to a matching running runtime."
  :type 'boolean)

(defcustom dynra-auto-jack-in nil
  "Whether opening a Python file starts Dynra when no runtime is running.
This is disabled by default because opening a file should not normally start a
project process.  Set it to non-nil for a CIDER-like automatic jack-in."
  :type 'boolean)

(defcustom dynra-source-roots '("src")
  "Project-relative directories that contain importable Python packages.
The first matching prefix is removed when mapping a file to its module."
  :type '(repeat directory))

(defcustom dynra-connect-timeout 2.0
  "Seconds to wait for a Dynra response."
  :type 'number)

(defcustom dynra-jack-in-timeout 15.0
  "Seconds to wait for a jack-in runtime to register."
  :type 'number)

(defcustom dynra-result-display 'both
  "Where evaluation results are displayed.
The value is one of `overlay', `echo', `both', or `errors-only'."
  :type '(choice (const overlay) (const echo) (const both) (const errors-only)))

(defcustom dynra-result-overlay-position 'at-eol
  "Position of inline evaluation results."
  :type '(choice (const at-eol) (const at-point)))

(defcustom dynra-result-overlay-duration 'command
  "Lifetime of evaluation result overlays.
A number is a duration in seconds.  `command' removes the overlay after the
next command, `change' removes it when the buffer changes, and nil keeps it."
  :type '(choice number (const command) (const change) (const nil)))

(defcustom dynra-result-prefix "=> "
  "Prefix shown before evaluation results."
  :type 'string)

(defface dynra-result-overlay-face
  '((((class color) (background light))
     :background "grey90" :box (:line-width -1 :color "yellow"))
    (((class color) (background dark))
     :background "grey10" :box (:line-width -1 :color "black")))
  "Face for successful inline results."
  :group 'dynra)

(defface dynra-error-overlay-face
  '((t :inherit error :box (:line-width -1)))
  "Face for failed inline results."
  :group 'dynra)

(defvar dynra-connected-hook nil
  "Hook run after a buffer connects to a Dynra runtime.")

(defvar dynra-disconnected-hook nil
  "Hook run after a buffer disconnects from a Dynra runtime.")

(defvar dynra--project-connections (make-hash-table :test #'equal)
  "Map canonical project roots to verified runtime records.")

(defvar dynra--jack-in-processes (make-hash-table :test #'equal)
  "Map canonical project roots to jack-in processes.")


(defvar-local dynra--result-overlays nil
  "Result overlays owned by the current buffer.")

(defvar-local dynra--jack-in-deadline nil)

(defun dynra--canonical-directory (path)
  "Return canonical directory name for PATH."
  (file-name-as-directory (file-truename (expand-file-name path))))

(defun dynra--project-root (&optional directory)
  "Return the Python project root for DIRECTORY or the current buffer."
  (let* ((start (or directory
                    (and buffer-file-name (file-name-directory buffer-file-name))
                    default-directory))
         (project (project-current nil start)))
    (dynra--canonical-directory
     (or (locate-dominating-file start "pyproject.toml")
         (locate-dominating-file start "manage.py")
         (and project (project-root project))
         start))))

(defun dynra--registry-directory ()
  "Return Dynra's runtime registry directory."
  (file-name-as-directory
   (expand-file-name
    (or (getenv "DYNRA_RUNTIME_DIR")
        (concat (or (getenv "XDG_CACHE_HOME") "~/.cache")
                "/dynra/runtimes")))))

(defun dynra--json-read-file (file)
  "Read FILE as a JSON alist, returning nil when it is invalid."
  (condition-case nil
      (let ((json-object-type 'alist)
            (json-array-type 'list)
            (json-key-type 'symbol)
            (json-false nil))
        (json-read-file file))
    (error nil)))

(defun dynra--runtime-records ()
  "Read locally registered Dynra runtime records."
  (let ((directory (dynra--registry-directory)))
    (when (file-directory-p directory)
      (delq nil (mapcar #'dynra--json-read-file
                        (directory-files directory t "\\.json\\'" t))))))

(defun dynra--runtime-process-live-p (record)
  "Return non-nil when RECORD's local runtime process is alive."
  (let ((pid (alist-get 'pid record)))
    (and (integerp pid)
         (> pid 0)
         (process-attributes pid))))

(defun dynra--runtime-for-project (root)
  "Return the newest live runtime record matching ROOT."
  (let* ((canonical (dynra--canonical-directory root))
         (matches
          (cl-remove-if-not
           (lambda (record)
             (let ((record-root (alist-get 'project_root record)))
               (and record-root
                    (dynra--runtime-process-live-p record)
                    (equal canonical (dynra--canonical-directory record-root)))))
           (dynra--runtime-records))))
    (car (sort matches
               (lambda (left right)
                 (string> (or (alist-get 'started_at left) "")
                          (or (alist-get 'started_at right) "")))))))

(defun dynra--request (record request &optional code timeout)
  "Send REQUEST and CODE to the runtime in RECORD and return its response."
  (let* ((host (or (alist-get 'host record) "127.0.0.1"))
         (port (alist-get 'port record))
         (runtime-id (alist-get 'runtime_id record))
         (request (append `((protocol . 1) (runtime_id . ,runtime-id)) request))
         (payload (concat "DYNRA " (json-encode request) "\n" (or code "")))
         (response "")
         process)
    (unless (integerp port)
      (error "Dynra runtime record has no valid port"))
    (condition-case err
        (progn
          (setq process
                (open-network-stream
                 "dynra-request" nil host port
                 :type 'plain
                 :coding 'utf-8-unix
                 :nowait nil))
          (set-process-query-on-exit-flag process nil)
          (set-process-filter
           process (lambda (_process chunk)
                     (setq response (concat response chunk))))
          (process-send-string process payload)
          (process-send-eof process)
          (let ((deadline (+ (float-time) (or timeout dynra-connect-timeout))))
            (while (and (process-live-p process) (< (float-time) deadline))
              (accept-process-output process 0.05)))
          (when (process-live-p process)
            (delete-process process)
            (error "Dynra request timed out"))
          (when (string-empty-p response)
            (error "Dynra returned no response"))
          (let ((json-object-type 'alist)
                (json-array-type 'list)
                (json-key-type 'symbol)
                (json-false nil))
            (json-read-from-string response)))
      (error
       (when (and process (process-live-p process))
         (delete-process process))
       (signal (car err) (cdr err))))))

(defun dynra--handshake (record)
  "Verify RECORD and return its authoritative runtime data."
  (let ((response (dynra--request record '((operation . "handshake")) nil 0.75)))
    (unless (alist-get 'success response)
      (error "%s" (or (alist-get 'error response) "Dynra handshake failed")))
    (alist-get 'runtime response)))

(defun dynra--connection-for-project (&optional root)
  "Return the shared runtime record for project ROOT."
  (gethash (dynra--project-root root) dynra--project-connections))

(defun dynra-connected-p ()
  "Return non-nil when the current project has a verified runtime."
  (when-let* ((connection (dynra--connection-for-project)))
    (condition-case nil
        (progn (dynra--handshake connection) t)
      (error nil))))

(defun dynra--set-connection (runtime)
  "Set RUNTIME as its project's shared connection."
  (let ((root (dynra--canonical-directory (alist-get 'project_root runtime))))
    (puthash root runtime dynra--project-connections)
    (dynra-mode 1)
    (run-hooks 'dynra-connected-hook)
    runtime))

;;;###autoload
(defun dynra-connect (&optional root)
  "Connect to the running Dynra runtime for project ROOT."
  (interactive)
  (let* ((root (dynra--project-root root))
         (record (or (dynra--runtime-for-project root)
                     (user-error "No running Dynra runtime for %s; use M-x dynra-jack-in"
                                 root)))
         (runtime (dynra--handshake record)))
    (unless (equal root (dynra--canonical-directory
                         (alist-get 'project_root runtime)))
      (user-error "Dynra runtime belongs to a different project"))
    (dynra--set-connection runtime)
    (message "Dynra connected: %s (%s:%s)"
             (alist-get 'runtime_id runtime)
             (alist-get 'host runtime)
             (alist-get 'port runtime))))

(defun dynra-disconnect ()
  "Disconnect the current project without stopping its runtime."
  (interactive)
  (remhash (dynra--project-root) dynra--project-connections)
  (dynra-mode -1)
  (dynra-clear-results)
  (run-hooks 'dynra-disconnected-hook)
  (message "Dynra project disconnected"))

(defun dynra--resolve-program (program)
  "Resolve PROGRAM using Emacs' PATH and common user install locations."
  (or (executable-find program)
      (locate-file
       program
       (append exec-path
               (mapcar #'expand-file-name
                       '("~/.local/bin" "~/.cargo/bin"
                         "/opt/homebrew/bin" "/usr/local/bin"
                         "/home/linuxbrew/.linuxbrew/bin")))
       exec-suffixes
       #'file-executable-p)
      (user-error
       "Cannot find executable %s; add it to Emacs PATH or set `dynra-command'"
       program)))


(defun dynra--jack-in-buffer-name (root)
  "Return the jack-in buffer name for ROOT."
  (format "*dynra:%s*"
          (file-name-nondirectory (directory-file-name root))))

(defun dynra--finish-jack-in (source-buffer root process deadline)
  "Complete jack-in for SOURCE-BUFFER and ROOT started by PROCESS before DEADLINE."
  (cond
   ((not (process-live-p process))
    (remhash root dynra--jack-in-processes)
    (message "Dynra jack-in exited; see %s" (buffer-name (process-buffer process))))
   ((> (float-time) deadline)
    (message "Dynra jack-in timed out; see %s" (buffer-name (process-buffer process))))
   ((let ((record (dynra--runtime-for-project root)))
      (when record
        (condition-case err
            (let ((runtime (dynra--handshake record)))
              (puthash root runtime dynra--project-connections)
              (when (buffer-live-p source-buffer)
                (with-current-buffer source-buffer
                  (dynra--set-connection runtime)))
              (message "Dynra jack-in ready: %s" (alist-get 'runtime_id runtime))
              t)
          (error
           (message "Dynra is registering: %s" (error-message-string err))
           nil)))))
   (t
    (run-at-time 0.1 nil #'dynra--finish-jack-in
                 source-buffer root process deadline))))

;;;###autoload
(defun dynra-jack-in (&optional root)
  "Start Dynra in project ROOT and connect the current buffer."
  (interactive)
  (let* ((source (current-buffer))
         (root (dynra--project-root root))
         (existing (dynra--runtime-for-project root)))
    (if existing
        (dynra-connect root)
      (let* ((default-directory root)
             (buffer (get-buffer-create (dynra--jack-in-buffer-name root)))
             (program (dynra--resolve-program (car dynra-command)))
             (arguments (cdr dynra-command))
             (process-connection-type t))
        (apply #'make-comint-in-buffer "dynra" buffer program nil arguments)
        (let* ((process (get-buffer-process buffer))
               (deadline (+ (float-time) dynra-jack-in-timeout)))
          (puthash root process dynra--jack-in-processes)
          (with-current-buffer buffer
            (setq-local default-directory root))
          (set-process-sentinel
           process
           (lambda (proc _event)
             (unless (process-live-p proc)
               (remhash root dynra--jack-in-processes))))
          (run-at-time 0.05 nil #'dynra--finish-jack-in
                       source root process deadline))
        (message "Starting Dynra in %s" root)))))

(defun dynra-switch-to-repl ()
  "Display this project's Dynra jack-in buffer."
  (interactive)
  (let* ((root (dynra--project-root))
         (process (gethash root dynra--jack-in-processes))
         (buffer (and process (process-buffer process))))
    (unless (buffer-live-p buffer)
      (user-error "No Emacs-managed Dynra process for this project"))
    (pop-to-buffer buffer)))

(defun dynra-quit ()
  "Stop the current project's runtime."
  (interactive)
  (let* ((root (dynra--project-root))
         (connection (dynra--ensure-connection))
         (response (dynra--request connection '((operation . "stop")))))
    (if (alist-get 'success response)
        (progn
          (remhash root dynra--project-connections)
          (dynra-mode -1)
          (dynra-clear-results)
          (run-hooks 'dynra-disconnected-hook)
          (message "Dynra runtime stopping"))
      (user-error "%s" (alist-get 'error response)))))

(defun dynra--module-name ()
  "Return the Python module represented by the current buffer."
  (unless buffer-file-name
    (user-error "Current buffer does not visit a file"))
  (let* ((root (dynra--project-root))
         (relative (file-relative-name (file-truename buffer-file-name) root))
         (source-root
          (cl-find-if
           (lambda (candidate)
             (string-prefix-p
              (file-name-as-directory candidate)
              relative))
           dynra-source-roots))
         (relative (if source-root
                       (string-remove-prefix
                        (file-name-as-directory source-root) relative)
                     relative))
         (without-extension (file-name-sans-extension relative))
         (module (replace-regexp-in-string "[/\\\\]" "." without-extension)))
    (setq module (replace-regexp-in-string "\\.__init__\\'" "" module))
    (if (string= module "__init__") "user" module)))

(defun dynra--ensure-connection ()
  "Return the current project's shared connection."
  (let ((root (dynra--project-root)))
    (or (gethash root dynra--project-connections)
        (progn
          (dynra-connect root)
          (gethash root dynra--project-connections)))))

(defun dynra--connection-refused-p (error-data)
  "Return non-nil when ERROR-DATA proves no connection was established."
  (string-match-p "connection refused"
                  (downcase (error-message-string error-data))))

(defun dynra--evaluate-request (module code)
  "Evaluate CODE in MODULE, reconnecting once on explicit connection refusal."
  (let ((request `((module . ,module) (create . :json-false))))
    (condition-case error-data
        (dynra--request (dynra--ensure-connection) request code 30.0)
      (error
       (if (dynra--connection-refused-p error-data)
           (let ((root (dynra--project-root)))
             (remhash root dynra--project-connections)
             (dynra-connect root)
             (dynra--request (dynra--ensure-connection)
                             request code 30.0))
         (signal (car error-data) (cdr error-data)))))))

(defun dynra--remove-overlay (overlay &rest _)
  "Delete OVERLAY and remove it from the owning buffer's list."
  (when (overlayp overlay)
    (let ((buffer (overlay-buffer overlay)))
      (when (buffer-live-p buffer)
        (with-current-buffer buffer
          (setq dynra--result-overlays (delq overlay dynra--result-overlays)))))
    (delete-overlay overlay)))

(defun dynra-clear-results ()
  "Remove all Dynra inline results in the current buffer."
  (interactive)
  (mapc #'dynra--remove-overlay (copy-sequence dynra--result-overlays))
  (setq dynra--result-overlays nil))

(defun dynra--expire-results-after-command ()
  "Arrange for result overlays to disappear after the next command."
  (remove-hook 'post-command-hook #'dynra--expire-results-after-command t)
  (add-hook 'post-command-hook #'dynra-clear-results nil t))

(defun dynra--make-result-overlay (text position errorp)
  "Show TEXT inline at POSITION, using the error face when ERRORP."
  (save-excursion
    (goto-char position)
    (let* ((at (if (eq dynra-result-overlay-position 'at-point)
                   position
                 (line-end-position)))
           (overlay (make-overlay at at (current-buffer)))
           (display (concat " " dynra-result-prefix
                            (replace-regexp-in-string "\n+\\'" "" text) " ")))
      (add-face-text-property
       0 (length display)
       (if errorp 'dynra-error-overlay-face 'dynra-result-overlay-face)
       t display)
      (overlay-put overlay 'after-string display)
      (overlay-put overlay 'dynra-result t)
      (overlay-put overlay 'modification-hooks
                   (list (lambda (ov &rest _) (dynra--remove-overlay ov))))
      (push overlay dynra--result-overlays)
      (pcase dynra-result-overlay-duration
        ((pred numberp)
         (run-at-time dynra-result-overlay-duration nil
                      #'dynra--remove-overlay overlay))
        ('command
         (remove-hook 'post-command-hook #'dynra-clear-results t)
         (add-hook 'post-command-hook #'dynra--expire-results-after-command nil t))
        ('change
         (overlay-put overlay 'modification-hooks
                      (list (lambda (ov &rest _) (dynra--remove-overlay ov))))))
      overlay)))

(defun dynra--response-text (response)
  "Return the user-facing text and error state for RESPONSE."
  (let* ((success (alist-get 'success response))
         (stdout (string-trim-right (or (alist-get 'stdout response) "")))
         (stderr (string-trim-right (or (alist-get 'stderr response) "")))
         (result (or (alist-get 'result response) ""))
         (error (or (alist-get 'error response) stderr)))
    (cons (cond
           ((not success) (or error "Evaluation failed"))
           ((not (string-empty-p stdout))
            (if (string-empty-p result) stdout (concat stdout "\n" result)))
           ((not (string-empty-p result)) result)
           (t "None"))
          (not success))))

(defun dynra--display-response (response position)
  "Display Dynra RESPONSE at POSITION."
  (pcase-let* ((`(,text . ,errorp) (dynra--response-text response))
               (overlayp
                (and (not errorp)
                     (memq dynra-result-display '(overlay both))))
               (echop
                (or (memq dynra-result-display '(echo both))
                    (and errorp (eq dynra-result-display 'errors-only))))
               (sync (alist-get 'source_sync response))
               (status (and sync (alist-get 'status sync)))
               (source-note
                (and status
                     (not (equal status "clean"))
                     (format "Dynra source: %s" status))))
    (when overlayp

      (dynra--make-result-overlay text position errorp))
    (cond
     ((and echop source-note)
      (message "%s%s | %s" dynra-result-prefix text source-note))
     (echop
      (message "%s%s" dynra-result-prefix text))
     (source-note
      (message "%s" source-note)))
    response))

(defun dynra-eval-region (start end)
  "Evaluate Python between START and END in its owning module."
  (interactive "r")
  (when (and buffer-file-name
             (not (file-exists-p buffer-file-name))
             (buffer-modified-p))
    (save-buffer))
  (let* ((module (dynra--module-name))
         (code (buffer-substring-no-properties start end))
         (response (dynra--evaluate-request module code)))
    (dynra--display-response response end)))

(defun dynra--treesit-top-level-node ()
  "Return the top-level Python syntax node containing or preceding point."
  (when (and (fboundp 'treesit-language-available-p)
             (treesit-language-available-p 'python))
    (condition-case nil
        (progn
          (unless (treesit-parser-list)
            (treesit-parser-create 'python))
          (save-excursion
            (skip-chars-backward " \t\r\n")
            (let* ((position
                    (if (and (< (point) (point-max))
                             (not (memq (char-after) '(?\s ?\t ?\r ?\n))))
                        (point)
                      (max (point-min) (1- (point)))))
                   (node (treesit-node-at position 'python))
                   parent)
              (while (and node
                          (setq parent (treesit-node-parent node))
                          (not (string= (treesit-node-type parent) "module")))
                (setq node parent))
              (when (and node parent
                         (string= (treesit-node-type parent) "module"))
                node))))
      (error nil))))

(defun dynra--treesit-top-level-bounds ()
  "Return bounds of the top-level Python form containing or preceding point."
  (when-let* ((node (dynra--treesit-top-level-node)))
    (cons (treesit-node-start node) (treesit-node-end node))))

(defun dynra--statement-bounds ()
  "Return bounds of the complete Python form at or preceding point.
Tree-sitter selects the containing top-level form, so compound statements,
loops, decorated definitions, functions, and classes are never truncated.
Fall back to `python-nav' when the Python grammar is unavailable."
  (or (dynra--treesit-top-level-bounds)
      (save-excursion
        (python-nav-beginning-of-statement)
        (let ((start (point)))
          (python-nav-end-of-statement)
          (cons start (point))))))

(defun dynra--definition-bounds ()
  "Return bounds of the top-level function or class containing point."
  (when-let* ((node (dynra--treesit-top-level-node))
              (type (treesit-node-type node)))
    (when (member type '("function_definition"
                         "class_definition"
                         "decorated_definition"))
      (cons (treesit-node-start node) (treesit-node-end node)))))

(defun dynra-eval-last-sexp ()
  "Evaluate the complete top-level Python form at or immediately before point."
  (interactive)
  (let ((bounds (condition-case nil
                    (dynra--statement-bounds)
                  (error nil))))
    (unless bounds
      (user-error "No Python form at point"))
    (dynra-eval-region (car bounds) (cdr bounds))))

(defun dynra-eval-defun-at-point ()
  "Evaluate the top-level Python function or class containing point."
  (interactive)
  (let ((bounds
         (or (dynra--definition-bounds)
             (save-excursion
               (python-nav-beginning-of-defun)
               (let ((start (point)))
                 (python-nav-end-of-defun)
                 (cons start (point)))))))
    (dynra-eval-region (car bounds) (cdr bounds))))

(defun dynra-eval-dwim ()
  "Evaluate the active region, or the function/class at point."
  (interactive)
  (if (use-region-p)
      (dynra-eval-region (region-beginning) (region-end))
    (dynra-eval-defun-at-point)))

(defun dynra-load-buffer ()
  "Evaluate the complete current Python buffer."
  (interactive)
  (dynra-eval-region (point-min) (point-max)))

(defun dynra-status ()
  "Display the current project's runtime status."
  (interactive)
  (let* ((connection (dynra--ensure-connection))
         (response (dynra--request connection '((operation . "status"))))
         (runtime (alist-get 'runtime response)))
    (message "Dynra %s | %s | module %s | generation %s"
             (alist-get 'runtime_id runtime)
             (alist-get 'project_root runtime)
             (alist-get 'current_module runtime)
             (alist-get 'generation runtime))))

(defun dynra-bootstrap ()
  "Run this project's configured Dynra bootstrap once."
  (interactive)
  (let* ((connection (dynra--ensure-connection))
         (response (dynra--request connection '((operation . "bootstrap")) nil 30.0)))
    (if (alist-get 'success response)
        (message "Dynra bootstrap: %s" (alist-get 'result response))
      (user-error "%s" (alist-get 'error response)))))

(defvar dynra-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-x C-e") #'dynra-eval-last-sexp)
    (define-key map (kbd "C-c C-e") #'dynra-eval-last-sexp)
    (define-key map (kbd "C-c C-c") #'dynra-eval-defun-at-point)
    (define-key map (kbd "C-M-x") #'dynra-eval-defun-at-point)
    (define-key map (kbd "C-c C-k") #'dynra-load-buffer)
    (define-key map (kbd "C-c C-r") #'dynra-eval-region)
    (define-key map (kbd "C-c C-z") #'dynra-switch-to-repl)
    (define-key map (kbd "C-c C-x j") #'dynra-jack-in)
    (define-key map (kbd "C-c C-x c") #'dynra-connect)
    (define-key map (kbd "C-c C-x b") #'dynra-bootstrap)
    (define-key map (kbd "C-c C-x s") #'dynra-status)
    (define-key map (kbd "C-c C-x q") #'dynra-quit)
    map)
  "Keymap for `dynra-mode'.")

(define-key python-mode-map (kbd "C-c C-x j") #'dynra-jack-in)
(when (boundp 'python-ts-mode-map)
  (define-key python-ts-mode-map (kbd "C-c C-x j") #'dynra-jack-in))

;;;###autoload
(define-minor-mode dynra-mode
  "CIDER-style live evaluation through a project-scoped Dynra runtime."
  :lighter (:eval (if (ignore-errors (dynra--connection-for-project))
                      " Dynra✓"
                    " Dynra"))
  :keymap dynra-mode-map)

(defun dynra--maybe-connect ()
  "Connect or jack in when a Python buffer opens."
  (when (and dynra-auto-connect buffer-file-name)
    (let* ((root (dynra--project-root))
           (record (dynra--runtime-for-project root)))
      (cond
       (record
        (condition-case err
            (dynra--set-connection (dynra--handshake record))
          (error (message "Dynra auto-connect failed: %s"
                          (error-message-string err)))))
       (dynra-auto-jack-in
        (dynra-jack-in root))))))

;;;###autoload
(define-minor-mode dynra-auto-mode
  "Globally discover or start Dynra for opened Python files."
  :global t
  (if dynra-auto-mode
      (progn
        (add-hook 'python-mode-hook #'dynra--maybe-connect)
        (add-hook 'python-ts-mode-hook #'dynra--maybe-connect))
    (remove-hook 'python-mode-hook #'dynra--maybe-connect)
    (remove-hook 'python-ts-mode-hook #'dynra--maybe-connect)))

(provide 'dynra)
;;; dynra.el ends here
