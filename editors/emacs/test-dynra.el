;;; test-dynra.el --- Tests for dynra.el -*- lexical-binding: t; -*-

(require 'ert)
(require 'json)
(require 'python)
(load (expand-file-name "dynra.el" (file-name-directory load-file-name)) nil t)

(defmacro dynra-test--with-project (&rest body)
  "Run BODY in a temporary Python project buffer."
  (declare (indent 0) (debug t))
  `(let* ((root (make-temp-file "dynra-emacs-project-" t))
          (package (expand-file-name "shop" root))
          (file (expand-file-name "pricing.py" package)))
     (unwind-protect
         (progn
           (make-directory package)
           (write-region "[project]\nname = \"sample\"\nversion = \"0.1.0\"\n"
                         nil (expand-file-name "pyproject.toml" root) nil 'silent)
           (write-region "value = 1\n" nil file nil 'silent)
           (with-temp-buffer
             (setq buffer-file-name file
                   default-directory package)
             (python-mode)
             ,@body))
       (delete-directory root t))))

(ert-deftest dynra-module-name-follows-project-layout ()
  (dynra-test--with-project
    (should (equal (dynra--module-name) "shop.pricing"))))

(ert-deftest dynra-module-name-removes-src-root ()
  (dynra-test--with-project
    (let* ((src-package (expand-file-name "src/shop" root))
           (src-file (expand-file-name "pricing.py" src-package)))
      (make-directory src-package t)
      (write-region "value = 1\n" nil src-file nil 'silent)
      (setq buffer-file-name src-file
            default-directory src-package)
      (should (equal (dynra--module-name) "shop.pricing")))))

(ert-deftest dynra-runtime-discovery-selects-newest-project-runtime ()
  (dynra-test--with-project
    (let* ((registry (make-temp-file "dynra-emacs-registry-" t))
           (process-environment (copy-sequence process-environment))
           (root (dynra--project-root)))
      (unwind-protect
          (progn
            (setenv "DYNRA_RUNTIME_DIR" registry)
            (dolist (record `(((runtime_id . "old")
                               (host . "127.0.0.1") (port . 1001)
                               (pid . ,(emacs-pid))
                               (project_root . ,root)
                               (started_at . "2026-01-01T00:00:00+00:00"))
                              ((runtime_id . "new")
                               (host . "127.0.0.1") (port . 1002)
                               (pid . ,(emacs-pid))
                               (project_root . ,root)
                               (started_at . "2026-02-01T00:00:00+00:00"))
                              ((runtime_id . "stale")
                               (host . "127.0.0.1") (port . 1003)
                               (pid . 99999999)
                               (project_root . ,root)
                               (started_at . "2026-03-01T00:00:00+00:00"))))
              (let ((id (alist-get 'runtime_id record)))
                (with-temp-file (expand-file-name (concat id ".json") registry)
                  (insert (json-encode record)))))
            (should (equal (alist-get 'runtime_id
                                      (dynra--runtime-for-project root))
                           "new")))
        (delete-directory registry t)))))

(ert-deftest dynra-response-text-preserves-stdout-and-result ()
  (should
   (equal (dynra--response-text
           '((success . t) (stdout . "created\n") (result . "42")))
          '("created\n42" . nil))))

(ert-deftest dynra-response-text-prefers-runtime-error ()
  (should
   (equal (dynra--response-text
           '((success . nil) (stderr . "trace") (error . "ValueError: bad")))
          '("ValueError: bad" . t))))

(ert-deftest dynra-connection-is-project-scoped-across-buffers ()
  (dynra-test--with-project
    (let* ((dynra--project-connections (make-hash-table :test #'equal))
           (root (dynra--project-root))
           (runtime `((runtime_id . "shared") (project_root . ,root))))
      (puthash root runtime dynra--project-connections)
      (should (eq (dynra--ensure-connection) runtime))
      (with-temp-buffer
        (setq buffer-file-name (expand-file-name "shop/other.py" root)
              default-directory (expand-file-name "shop" root))
        (should (eq (dynra--ensure-connection) runtime))))))

(ert-deftest dynra-result-overlay-is-inline-and-clearable ()
  (with-temp-buffer
    (insert "answer = 42")
    (let ((dynra-result-overlay-duration nil))
      (dynra--make-result-overlay "42" (point-max) nil)
      (should (= (length dynra--result-overlays) 1))
      (should (equal (overlay-get (car dynra--result-overlays) 'after-string)
                     " => 42 "))
      (should (overlay-buffer (car dynra--result-overlays)))
      (should (= (overlay-start (car dynra--result-overlays)) (point-max)))
      (dynra-clear-results)
      (should-not dynra--result-overlays))))

(ert-deftest dynra-command-duration-clears-after-next-command ()
  (with-temp-buffer
    (insert "x = 2")
    (let ((dynra-result-overlay-duration 'command)
          (this-command 'dynra-eval-last-sexp))
      (dynra--make-result-overlay "2" (point-max) nil)
      (run-hooks 'post-command-hook)
      (should (overlay-buffer (car dynra--result-overlays)))
      (run-hooks 'post-command-hook)
      (should-not dynra--result-overlays))))

(ert-deftest dynra-errors-do-not-create-inline-overlays ()
  (with-temp-buffer
    (insert "broken()")
    (let ((dynra-result-display 'both))
      (dynra--display-response
       '((success . nil) (error . "NameError: broken")) (point-max))
      (should-not dynra--result-overlays))))

(ert-deftest dynra-assignment-without-result-displays-none ()
  (should
   (equal (dynra--response-text '((success . t) (result)))
          '("None" . nil))))

(ert-deftest dynra-first-evaluation-saves-a-new-module-file ()
  (let* ((root (make-temp-file "dynra-emacs-new-module-" t))
         (file (expand-file-name "module2.py" root)))
    (unwind-protect
        (with-temp-buffer
          (setq buffer-file-name file
                default-directory root)
          (insert "x = 2")
          (set-buffer-modified-p t)
          (cl-letf (((symbol-function 'dynra--ensure-connection)
                     (lambda () 'connection))
                    ((symbol-function 'dynra--module-name)
                     (lambda () "module2"))
                    ((symbol-function 'dynra--request)
                     (lambda (&rest _) '((success . t))))
                    ((symbol-function 'dynra--display-response)
                     (lambda (response _) response)))
            (dynra-eval-region (point-min) (point-max)))
          (should (file-exists-p file))
          (should (equal (with-temp-buffer
                           (insert-file-contents file)
                           (buffer-string))
                         "x = 2")))
      (delete-directory root t))))

(defun dynra-test--form-at (source needle bounds-function)
  "Return text selected by BOUNDS-FUNCTION at NEEDLE in SOURCE."
  (with-temp-buffer
    (insert source)
    (goto-char (point-min))
    (search-forward needle)
    (pcase-let ((`(,start . ,end) (funcall bounds-function)))
      (buffer-substring-no-properties start end))))


(ert-deftest dynra-treesit-selects-complete-expression ()
  (skip-unless (treesit-language-available-p 'python))
  (should
   (equal (dynra-test--form-at
           "value = 1\n40 + 2\n" "40 + 2" #'dynra--statement-bounds)
          "40 + 2")))

(ert-deftest dynra-treesit-selects-complete-loop-from-body ()
  (skip-unless (treesit-language-available-p 'python))
  (should
   (equal (dynra-test--form-at
           "for item in range(3):\n    print(item)\n"
           "print" #'dynra--statement-bounds)
          "for item in range(3):\n    print(item)")))

(ert-deftest dynra-treesit-selects-complete-function-from-body ()
  (skip-unless (treesit-language-available-p 'python))
  (let ((source "def greet(name):\n    return f\"Hello {name}\"\n"))
    (should
     (equal (dynra-test--form-at
             source "return" #'dynra--statement-bounds)
            (string-trim-right source)))
    (should
     (equal (dynra-test--form-at
             source "return" #'dynra--definition-bounds)
            (string-trim-right source)))))

(ert-deftest dynra-treesit-selects-complete-class-from-method ()
  (skip-unless (treesit-language-available-p 'python))
  (let ((source "class Counter:\n    def value(self):\n        return 42\n"))
    (should
     (equal (dynra-test--form-at
             source "return" #'dynra--statement-bounds)
            (string-trim-right source)))
    (should
     (equal (dynra-test--form-at
             source "return" #'dynra--definition-bounds)
            (string-trim-right source)))))


(ert-deftest dynra-jack-in-key-is-available-before-connection ()
  (should (eq (lookup-key python-mode-map (kbd "C-c C-x j"))
              #'dynra-jack-in))
  (when (boundp 'python-ts-mode-map)
    (should (eq (lookup-key python-ts-mode-map (kbd "C-c C-x j"))
                #'dynra-jack-in))))

(ert-deftest dynra-resolves-executable-from-emacs-path ()
  (let* ((directory (make-temp-file "dynra-emacs-bin-" t))
         (program (expand-file-name "uv" directory))
         (exec-path (cons directory exec-path)))
    (unwind-protect
        (progn
          (with-temp-file program (insert "#!/bin/sh\nexit 0\n"))
          (set-file-modes program #o755)
          (should (equal (dynra--resolve-program "uv") program)))
      (delete-directory directory t))))

(ert-deftest dynra-mode-exposes-cider-style-bindings ()
  (should (eq (lookup-key dynra-mode-map (kbd "C-x C-e"))
              #'dynra-eval-last-sexp))
  (should (eq (lookup-key dynra-mode-map (kbd "C-c C-c"))
              #'dynra-eval-defun-at-point))
  (should (eq (lookup-key dynra-mode-map (kbd "C-c C-k"))
              #'dynra-load-buffer))
  (should (eq (lookup-key dynra-mode-map (kbd "C-c C-x j"))
              #'dynra-jack-in)))

(provide 'test-dynra)
;;; test-dynra.el ends here
