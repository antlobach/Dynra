;;; dynra.el --- CIDER-like REPL for Dynra -*- lexical-binding: t; -*-

(require 'cl-lib)
(require 'json)
(require 'python nil t)
(require 'subr-x)

(declare-function python-nav-beginning-of-defun "python")
(declare-function python-nav-beginning-of-statement "python")
(declare-function python-nav-end-of-defun "python")
(declare-function python-nav-end-of-statement "python")

(defgroup dynra nil
  "CIDER-like REPL for Dynra."
  :group 'tools)

(defcustom dynra-host "127.0.0.1"
  "Host for Dynra REPL server."
  :type 'string)

(defcustom dynra-port 9000
  "Port for Dynra REPL server."
  :type 'integer)

(defface dynra-result-overlay-face
  '((((class color) (background light))
     :background "grey90" :box (:line-width -1 :color "yellow"))
    (((class color) (background dark))
     :background "grey10" :box (:line-width -1 :color "black")))
  "Face used to display evaluation results at the end of line.")

(defface dynra-error-overlay-face
  '((((class color) (background light))
     :background "orange red"
     :extend t)
    (((class color) (background dark))
     :background "firebrick"
     :extend t))
  "Face used to display evaluation errors at the end of line.")

(defcustom dynra-overlays-use-font-lock t
  "If non-nil, result overlays use font-lock style face prepending."
  :type 'boolean)

(defcustom dynra-use-overlays 'both
  "Whether to display evaluation results with overlays.
If t, use overlays.
If `errors-only', only errors use overlays.
If nil, display in echo area.
If `both', display in both places."
  :type '(choice (const :tag "Display using overlays" t)
                 (const :tag "Display in echo area" nil)
                 (const :tag "Both" both)
                 (const :tag "Errors only" errors-only)))

(defcustom dynra-result-overlay-position 'at-eol
  "Where to display result overlays.
If `at-eol', display at the end of line.
If `at-point', display at point."
  :type '(choice (const :tag "End of line" at-eol)
                 (const :tag "At point" at-point)))

(defcustom dynra-eval-result-prefix "=> "
  "Prefix displayed before a result value."
  :type 'string)

(defcustom dynra-eval-result-duration 'command
  "Duration, in seconds, of Dynra eval-result overlays.
If nil, overlays last indefinitely.
If the symbol `command', they're erased after the next command.
If the symbol `change', they last until the next buffer change."
  :type '(choice (integer :tag "Duration in seconds")
                 (const :tag "Until next command" command)
                 (const :tag "Until next buffer change" change)
                 (const :tag "Last indefinitely" nil)))

(defcustom dynra-eval-indented-scope 'defun
  "How `dynra-eval-last-expression' behaves on indented lines.
If `defun', evaluate the surrounding defun/class.
If `statement', evaluate only the statement at point."
  :type '(choice (const :tag "Defun/class" defun)
                 (const :tag "Current statement" statement)))

(defvar dynra-mode-map (make-sparse-keymap)
  "Keymap for `dynra-mode'.")

;; Refresh map on every load so re-evaluating this file updates bindings.
(setq dynra-mode-map
      (let ((map (make-sparse-keymap)))
        (define-key map (kbd "C-c C-x") #'dynra-connect)
        ;; CIDER-like eval flow
        (define-key map (kbd "C-x C-e") #'dynra-eval-last-sexp)
        (define-key map (kbd "C-c C-e") #'dynra-eval-last-sexp)
        (define-key map (kbd "C-x C-s") #'dynra-eval-last-expression)
        (define-key map (kbd "C-c C-f") #'dynra-eval-form-at-point)
        (define-key map (kbd "C-c C-c") #'dynra-eval-defun-at-point)
        (define-key map (kbd "C-M-x") #'dynra-eval-defun-at-point)
        (define-key map (kbd "C-c C-k") #'dynra-load-buffer)
        (define-key map (kbd "C-c C-r") #'dynra-eval-region)
        (define-key map (kbd "C-c C-m") #'dynra-show-module)
        ;; Alternate bindings in case of severe conflicts
        (define-key map (kbd "C-c M-e") #'dynra-eval-last-sexp)
        (define-key map (kbd "C-c M-c") #'dynra-eval-defun-at-point)
        map))

;;;###autoload
(define-minor-mode dynra-mode
  "Global minor mode for Dynra REPL interaction.
When active, its keybindings take precedence over major modes."
  :global t
  :lighter " Dynra"
  :keymap dynra-mode-map)

(defun dynra--send-to-repl (code)
  "Send CODE to the Dynra REPL and return the response as an alist."
  (let* ((host dynra-host)
         (port dynra-port)
         (response-data ""))
    (condition-case err
        (let ((proc (open-network-stream "dynra" nil host port)))
          (set-process-coding-system proc 'utf-8 'utf-8)
          (set-process-filter proc (lambda (_proc s) (setq response-data (concat response-data s))))
          (process-send-string proc code)
          (process-send-eof proc)
          (while (accept-process-output proc 1))
          (with-temp-buffer
            (insert response-data)
            (goto-char (point-min))
            (if (= (point-min) (point-max))
                (error "No response from Dynra REPL")
              (let ((json-object-type 'alist)
                    (json-array-type 'vector)
                    (json-key-type 'symbol))
                (json-read)))))
      (error (list (cons 'error (error-message-string err)))))))

(defun dynra--delete-overlay (ov &rest _)
  "Safely delete overlay OV."
  (ignore-errors (delete-overlay ov)))

(defun dynra--make-overlay (l r type &rest props)
  "Place an overlay between L and R and return it."
  (let ((o (make-overlay l (or r l) (current-buffer))))
    (overlay-put o 'category type)
    (overlay-put o 'dynra-temporary t)
    (while props
      (overlay-put o (pop props) (pop props)))
    (push #'dynra--delete-overlay (overlay-get o 'modification-hooks))
    o))

(defun dynra--remove-result-overlay (&rest _)
  "Remove result overlay from current buffer."
  (let ((hook (pcase dynra-eval-result-duration
                (`command 'post-command-hook)
                (`change 'after-change-functions))))
    (remove-hook hook #'dynra--remove-result-overlay 'local))
  (remove-overlays nil nil 'category 'dynra-result))

(defun dynra--remove-result-overlay-after-command ()
  "Schedule result overlay removal after the next command."
  (remove-hook 'post-command-hook #'dynra--remove-result-overlay-after-command 'local)
  (add-hook 'post-command-hook #'dynra--remove-result-overlay nil 'local))

(cl-defun dynra--make-result-overlay (value &rest props &key where duration
                                           (type 'dynra-result)
                                           (format (concat " " dynra-eval-result-prefix "%s "))
                                           (prepend-face 'dynra-result-overlay-face)
                                           &allow-other-keys)
  "Place an overlay displaying VALUE at WHERE."
  (declare (indent 1))
  (while (keywordp (car props))
    (setq props (cdr (cdr props))))
  (let ((buffer (cond
                 ((markerp where) (marker-buffer where))
                 ((markerp (car-safe where)) (marker-buffer (car where)))
                 (t (current-buffer)))))
    (when (buffer-live-p buffer)
      (with-current-buffer buffer
        (save-excursion
          (when (number-or-marker-p where)
            (goto-char where))
          (skip-chars-backward "\r\n[:blank:]")
          (let* ((beg (if (consp where)
                          (car where)
                        (save-excursion
                          (when (fboundp 'python-nav-beginning-of-statement)
                            (ignore-errors
                              (python-nav-beginning-of-statement)))
                          (point))))
                 (end (if (consp where)
                          (cdr where)
                        (pcase dynra-result-overlay-position
                          ('at-eol (line-end-position))
                          ('at-point (point)))))
                 (display-string (format (propertize format 'face 'default) value))
                 (truncation-threshold (* 3 (window-width)))
                 (o nil))
            (remove-overlays beg end 'category type)
            (funcall (if dynra-overlays-use-font-lock
                         #'font-lock-prepend-text-property
                       #'put-text-property)
                     0 (length display-string)
                     'face prepend-face
                     display-string)
            (when (or (string-match "\n." display-string)
                      (> (length display-string) truncation-threshold)
                      (> (string-width display-string)
                         (- (window-width) (current-column))))
              (setq display-string (concat " \n" display-string)))
            (when (or (> (length display-string) truncation-threshold)
                      (> (string-width display-string) truncation-threshold))
              (setq display-string
                    (concat (substring display-string 0 truncation-threshold)
                            "...")))
            (put-text-property 0 1 'cursor 0 display-string)
            (setq o (apply #'dynra--make-overlay
                           beg end type
                           'after-string display-string
                           props))
            (pcase duration
              ((pred numberp) (run-at-time duration nil #'dynra--delete-overlay o))
              (`command
               (remove-hook 'post-command-hook #'dynra--remove-result-overlay 'local)
               (if this-command
                   (add-hook 'post-command-hook
                             #'dynra--remove-result-overlay-after-command
                             nil 'local)
                 (dynra--remove-result-overlay-after-command)))
              (`change
               (add-hook 'after-change-functions
                         #'dynra--remove-result-overlay
                         nil 'local)))
            (when-let* ((win (get-buffer-window buffer)))
              (when (and (<= (window-start win) (point) (window-end win))
                         (or (< (+ (current-column) (string-width display-string))
                                (window-width win))
                             (not truncate-lines)))
                o))))))))

(defun dynra--display-interactive-eval-result (value value-type &optional point overlay-face)
  "Display eval VALUE from VALUE-TYPE at POINT."
  (let* ((value (string-trim-right value))
         (used-overlay (when (and point
                                  dynra-use-overlays
                                  (if (eq value-type 'error)
                                      t
                                    (not (eq dynra-use-overlays 'errors-only))))
                         (dynra--make-result-overlay value
                           :where point
                           :duration dynra-eval-result-duration
                           :type 'dynra-result
                           :prepend-face (or overlay-face 'dynra-result-overlay-face))))
         (msg (format "%s%s" dynra-eval-result-prefix value))
         (max-msg-length (* (floor (* (frame-height) max-mini-window-height))
                            (frame-width)))
         (msg (if (> (string-width msg) max-msg-length)
                  (format "%s..." (substring msg 0 (- max-msg-length 3)))
                msg)))
    (message "%s"
             (propertize msg
                         'invisible (and used-overlay
                                         (not (eq dynra-use-overlays 'both)))))))

(defun dynra--display-result (response pos)
  "Display RESPONSE from Dynra REPL at POS."
  (if (alist-get 'error response)
      (message "❌ Dynra Error: %s" (alist-get 'error response))
    (let* ((stdout (alist-get 'stdout response))
           (stderr (alist-get 'stderr response))
           (success (alist-get 'success response))
           (result (alist-get 'result response))
           ;; Strip ANSI escape codes from all outputs
           (stdout (when stdout (replace-regexp-in-string "\x1b\\[[0-9;]*[mK]" "" stdout)))
           (stderr (when stderr (replace-regexp-in-string "\x1b\\[[0-9;]*[mK]" "" stderr)))
           (result (when result (replace-regexp-in-string "\x1b\\[[0-9;]*[mK]" "" result)))
           ;; Clean up stdout (remove Out[N]: prefix)
           (clean-stdout (if (and stdout (string-match "Out\\[[0-9]+\\]: \\(.*\\)" stdout))
                             (match-string 1 stdout)
                           stdout))
           ;; Prioritize clean stdout if it has content, then result
           (raw-text (cond
                      ((and clean-stdout (not (string= (string-trim clean-stdout) ""))) (string-trim clean-stdout))
                      ((and result (not (string= result "None")) (not (string= result ""))) result)
                      (t "None")))
           ;; Remove outer quotes from repr() if it is a simple string
           (raw-text (if (string-match "^'\\(.*\\)'$" raw-text)
                         (match-string 1 raw-text)
                       raw-text))
           ;; Clean up text for overlay (one line, truncated)
           (display-text (replace-regexp-in-string "\n" " " raw-text))
           ;; Strip cell markers for cleaner display
           (display-text (replace-regexp-in-string "Cell In \\[[0-9]+\\], line [0-9]+" "" display-text))
           (display-text (if (> (length display-text) 50)
                             (concat (substring display-text 0 47) "...")
                           (string-trim display-text))))
      (when (and stderr (not (string= stderr "")))
        (message "⚠️ STDERR: %s" stderr))
      (if success
          (dynra--display-interactive-eval-result raw-text 'value pos)
        (dynra--display-interactive-eval-result
         (or display-text stderr "")
         'error
         pos
         'dynra-error-overlay-face)))))

;;;###autoload
(defun dynra-connect (host port)
  "Connect to a Dynra REPL server at HOST and PORT."
  (interactive
   (list (read-string "Host: " dynra-host)
         (read-number "Port: " dynra-port)))
  (setq dynra-host host
        dynra-port port)
  (condition-case nil
      (let ((response (dynra--send-to-repl "1+1")))
        (if (alist-get 'error response)
            (message "❌ Failed to connect to Dynra at %s:%d: %s" host port (alist-get 'error response))
          (message "✅ Connected to Dynra REPL at %s:%d" host port)
          ;; Turn on global dynra-mode so it works everywhere instantly
          (dynra-mode 1)))
    (error (message "❌ Could not reach Dynra server at %s:%d" host port))))

(defun dynra--get-module-name ()
  "Calculate the Python module name for the current buffer."
  (when buffer-file-name
    (let* ((root (dynra--get-project-root))
           (rel-path (file-relative-name buffer-file-name root))
           ;; Remove .py extension and convert path separators to dots
           (module-name (file-name-sans-extension
                         (replace-regexp-in-string "[/\\\\]" "." rel-path)))
           ;; __init__.py represents the package itself.
           (module-name (if (string-suffix-p ".__init__" module-name)
                            (substring module-name 0 (- (length module-name) 9))
                          module-name)))
      module-name)))

(defun dynra--get-project-root ()
  "Get the project root directory for the current buffer."
  (when buffer-file-name
    (or ;; Prefer Python project markers before VCS root for better module mapping in monorepos.
     (locate-dominating-file buffer-file-name "pyproject.toml")
     (locate-dominating-file buffer-file-name "setup.py")
     (locate-dominating-file buffer-file-name "requirements.txt")
     (locate-dominating-file buffer-file-name ".git")
     ;; For Python packages, walk all the way to the top-most package
     (let* ((dir (file-name-directory buffer-file-name))
            (init-dir dir))
       ;; Find first __init__.py
       (while (and init-dir (file-exists-p (concat init-dir "__init__.py")))
         (setq dir init-dir)
         (setq init-dir (file-name-directory (directory-file-name init-dir))))
       dir)
     ;; Ultimate fallback
     (file-name-directory buffer-file-name))))

(defun dynra-eval-region (start end)
  "Evaluate region from START to END in Dynra REPL."
  (interactive "r")
  (let* ((code (buffer-substring-no-properties start end))
         (module (dynra--get-module-name))
         (root (dynra--get-project-root))
         ;; Build code: add path, refresh import caches, import, then switch module.
         ;; Do not clear sys.modules here; we want live definitions to persist across evals.
         (setup-code (when (and module root)
                       (format "import sys, importlib\nsys.path.insert(0, '%s')\nimportlib.invalidate_caches()"
                               root)))
         ;; Finally switch to the module namespace and run user code
         (final-code (cond
                      (module (format "%s\nin_md('%s')\n%s"
                                      (or setup-code "import sys")
                                      module
                                      code))
                      (root (format "import sys; sys.path.insert(0, '%s')\n%s" root code))
                      (t code))))
    (dynra--display-result (dynra--send-to-repl final-code) end)))

(defun dynra--statement-bounds-at-point ()
  "Return statement bounds at point as (START . END), or nil."
  (or (when (and (fboundp 'python-nav-beginning-of-statement)
                 (fboundp 'python-nav-end-of-statement))
        (save-excursion
          (ignore-errors
            (python-nav-beginning-of-statement)
            (let ((s (point)))
              (python-nav-end-of-statement)
              (cons s (point))))))
      (when (fboundp 'bounds-of-thing-at-point)
        (bounds-of-thing-at-point 'line))))

(defun dynra--sexp-bounds-at-point ()
  "Return sexp/form bounds at point as (START . END), or nil."
  (or (bounds-of-thing-at-point 'sexp)
      (dynra--statement-bounds-at-point)))

(defun dynra--last-sexp-bounds-before-point ()
  "Return bounds for sexp/form before point as (START . END), or nil."
  (or (when (derived-mode-p 'python-mode 'python-ts-mode)
        (save-excursion
          (let ((stmt (dynra--statement-bounds-at-point)))
            (when (and stmt (<= (cdr stmt) (point)))
              stmt))))
      (save-excursion
        (ignore-errors
          (backward-sexp)
          (let ((s (point)))
            (forward-sexp)
            (cons s (point)))))
      (save-excursion
        (ignore-errors
          (backward-sexp)
          (dynra--statement-bounds-at-point)))))

(defun dynra-eval-last-sexp ()
  "Evaluate the form directly before point (CIDER-style)."
  (interactive)
  (if-let* ((b (dynra--last-sexp-bounds-before-point)))
      (dynra-eval-region (car b) (cdr b))
    (user-error "No form found before point")))

(defun dynra-eval-form-at-point ()
  "Evaluate the form at point."
  (interactive)
  (if-let* ((b (dynra--sexp-bounds-at-point)))
      (dynra-eval-region (car b) (cdr b))
    (user-error "No form found at point")))

(defun dynra-eval-last-expression (&optional arg)
  "Evaluate context-aware expression at point.
With region active, evaluate region.
For Python: indented lines follow `dynra-eval-indented-scope';
top-level lines evaluate statement.
With prefix ARG, force statement-only evaluation on indented lines."
  (interactive "P")
  (if (use-region-p)
      (dynra-eval-region (region-beginning) (region-end))
    ;; Use python-nav as primary and tree-sitter-aware fallbacks where available.
    (save-excursion
      (cl-labels
          ((treesit-defun-bounds
             ()
             (when (and (derived-mode-p 'python-ts-mode)
                        (fboundp 'treesit-node-at)
                        (fboundp 'treesit-node-type)
                        (fboundp 'treesit-node-start)
                        (fboundp 'treesit-node-end)
                        (fboundp 'treesit-node-parent))
               (let ((node (treesit-node-at (point))))
                 (while (and node
                             (not (member (treesit-node-type node)
                                          '("function_definition"
                                            "async_function_definition"
                                            "class_definition"))))
                   (setq node (treesit-node-parent node)))
                 (when node
                   (cons (treesit-node-start node)
                         (treesit-node-end node))))))
          (treesit-top-level-node-bounds
             ()
             (when (and (derived-mode-p 'python-ts-mode)
                        (fboundp 'treesit-node-at)
                        (fboundp 'treesit-node-type)
                        (fboundp 'treesit-node-parent)
                        (fboundp 'treesit-node-start)
                        (fboundp 'treesit-node-end))
               (let ((node (treesit-node-at (point))))
                 (while (and (treesit-node-parent node)
                             (not (string= (treesit-node-type (treesit-node-parent node))
                                           "module")))
                   (setq node (treesit-node-parent node)))
                 (when node
                   (cons (treesit-node-start node)
                         (treesit-node-end node)))))))
        (let ((evaluate-defun (if arg nil (eq dynra-eval-indented-scope 'defun))))
          (condition-case nil
            (if (derived-mode-p 'python-mode 'python-ts-mode)
                (progn
                  (back-to-indentation)
                  (if (and (> (current-column) 0)
                           (or (and (fboundp 'python-nav-beginning-of-defun)
                                    (fboundp 'python-nav-end-of-defun))
                               (treesit-defun-bounds))
                           evaluate-defun)
                      (let ((bounds (or (when (and (fboundp 'python-nav-beginning-of-defun)
                                                   (fboundp 'python-nav-end-of-defun))
                                          (save-excursion
                                            (python-nav-beginning-of-defun)
                                            (let ((s (point)))
                                              (python-nav-end-of-defun)
                                              (cons s (point)))))
                                        (treesit-defun-bounds))))
                        (dynra-eval-region (car bounds) (cdr bounds)))
                    (let ((bounds (or (when (and (fboundp 'python-nav-beginning-of-statement)
                                                 (fboundp 'python-nav-end-of-statement))
                                        (save-excursion
                                          (python-nav-beginning-of-statement)
                                          (let ((s (point)))
                                            (python-nav-end-of-statement)
                                            (cons s (point)))))
                                      (treesit-top-level-node-bounds)
                                      (cons (line-beginning-position) (line-end-position)))))
                      (dynra-eval-region (car bounds) (cdr bounds)))))
              (dynra-eval-region (line-beginning-position) (line-end-position)))
            (error
             (dynra-eval-region (line-beginning-position) (line-end-position)))))))))

(defun dynra-eval-defun-at-point ()
  "Evaluate the top-level form at point (class or function)."
  (interactive)
  (save-excursion
    (cl-labels
        ((treesit-defun-bounds
           ()
           (when (and (derived-mode-p 'python-ts-mode)
                      (fboundp 'treesit-node-at)
                      (fboundp 'treesit-node-type)
                      (fboundp 'treesit-node-start)
                      (fboundp 'treesit-node-end)
                      (fboundp 'treesit-node-parent))
             (let ((node (treesit-node-at (point))))
               (while (and node
                           (not (member (treesit-node-type node)
                                        '("function_definition"
                                          "async_function_definition"
                                          "class_definition"))))
                 (setq node (treesit-node-parent node)))
               (when node
                 (cons (treesit-node-start node)
                       (treesit-node-end node)))))))
      (condition-case nil
          (if (derived-mode-p 'python-mode 'python-ts-mode)
              (let ((bounds (or (when (and (fboundp 'python-nav-beginning-of-defun)
                                           (fboundp 'python-nav-end-of-defun))
                                  (save-excursion
                                    (python-nav-beginning-of-defun)
                                    (let ((s (point)))
                                      (python-nav-end-of-defun)
                                      (cons s (point)))))
                                (treesit-defun-bounds))))
                (if bounds
                    (dynra-eval-region (car bounds) (cdr bounds))
                  (dynra-eval-region (line-beginning-position) (line-end-position))))
            (dynra-eval-region (line-beginning-position) (line-end-position)))
        (error
         (dynra-eval-region (line-beginning-position) (line-end-position)))))))

(defun dynra-load-buffer ()
  "Load current buffer into Dynra REPL."
  (interactive)
  (dynra-eval-region (point-min) (point-max)))

(defun dynra-show-module ()
  "Show the current Python module context in both Dynra and Emacs."
  (interactive)
  (let* ((response (dynra--send-to-repl "__name__"))
         (backend-mod (alist-get 'result response))
         (local-mod (dynra--get-module-name)))
    (message "Dynra Backend: %s | Emacs Buffer: %s" 
             (or backend-mod "Unknown")
             (or local-mod "None (Global)"))))

(provide 'dynra)
;;; dynra.el ends here
