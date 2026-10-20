// Preserve the reference desktop composition at every window width.
// Keep these virtual viewport dimensions in sync with studio.css.
(() => {
    const referenceWidth = 1840;
    function resizeStudio() {
        const scale = document.documentElement.clientWidth / referenceWidth;
        if (scale <= 0) return;
        const style = document.documentElement.style;
        style.setProperty('--studio-scale', String(scale));
        style.setProperty('--studio-width', `${referenceWidth}px`);
        style.setProperty('--studio-height', `${window.innerHeight / scale}px`);
    }
    resizeStudio();
    window.addEventListener('resize', resizeStudio);
})();
