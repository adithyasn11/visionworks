// frontend/app/components/IntroSplash.jsx
//
// The brand intro shown when someone first opens the site (once per browser session).
//
// Pure markup + CSS (globals.css, "Intro splash"), rendered by the server, so it is on screen from the very
// first paint — before any JavaScript has loaded — and it always finishes and gets out of the way on its own
// (about 1.7 s), even if a script is slow. Whether it shows is decided by a tiny inline script in the root
// layout that runs before paint: on later pages of the same visit it adds `intro-seen` to <html> and the
// overlay is never drawn. People who ask for reduced motion never see it.

export default function IntroSplash() {
  return (
    <div id="vw-intro" aria-hidden="true">
      <div className="vw-intro-inner">
        <div className="vw-intro-mark">
          <span className="vw-intro-dot" />
          <span className="vw-intro-scan" />
        </div>
        <div className="vw-intro-word">VisionWorks</div>
        <div className="vw-intro-tag">Workplace safety compliance</div>
        <div className="vw-intro-bar"><span /></div>
      </div>
    </div>
  );
}
