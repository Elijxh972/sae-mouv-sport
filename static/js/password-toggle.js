(function () {
    function init() {
        document.querySelectorAll('.password-field-wrap').forEach(function (wrap) {
            var input = wrap.querySelector('input');
            var btn = wrap.querySelector('.password-toggle');
            var eyeShow = btn && btn.querySelector('.password-toggle-icon--show');
            var eyeHide = btn && btn.querySelector('.password-toggle-icon--hide');
            if (!input || !btn) return;

            btn.addEventListener('click', function () {
                var willShow = input.type === 'password';
                input.type = willShow ? 'text' : 'password';
                btn.setAttribute('aria-label', willShow ? 'Masquer le mot de passe' : 'Afficher le mot de passe');
                btn.setAttribute('aria-pressed', willShow ? 'true' : 'false');
                if (eyeShow && eyeHide) {
                    eyeShow.hidden = willShow;
                    eyeHide.hidden = !willShow;
                }
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
