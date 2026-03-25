// Crée l'overlay une seule fois
const overlay = document.createElement('div');
overlay.className = 'page-transition-overlay';
overlay.innerHTML = '<img src="/static/img/logo.png" alt="Chargement...">';
document.body.appendChild(overlay);

// Affiche l'overlay sur tous les clics de liens (sauf ancres et liens externes)
document.addEventListener('click', function(e) {
    const link = e.target.closest('a');
    if (!link) return;
    const href = link.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('http') || href.startsWith('mailto') || link.target === '_blank') return;
    e.preventDefault();
    overlay.classList.add('active');
    setTimeout(() => { window.location.href = href; }, 500);
});

// Affiche l'overlay sur les soumissions de formulaires
document.addEventListener('submit', function() {
    overlay.classList.add('active');
});

// Cache l'overlay quand la page est chargée (retour arrière navigateur)
window.addEventListener('pageshow', function() {
    overlay.classList.remove('active');
});
