// frontend/app/camera/[code]/page.jsx
//
// The page a phone opens after scanning the QR code. This IS the camera.
//
// WHY IT IS A PUBLIC ROUTE
//
// No sign-in, deliberately. The phone arrives here from a QR scan, before any
// session exists, and asking somebody to type an email and password on a phone
// keyboard to attach a camera would defeat the point of scanning a code.
//
// The code in the URL is the credential: eight characters, single-use, expiring
// in five minutes, and only reachable from inside your own network. The room it
// opens was created by somebody already signed in, and it relays to that person
// and nobody else. That is honest security for a LAN tool; it is not enough to
// expose the port to the internet, which the backend logs a warning about.
//
// WHAT THIS PAGE MUST GET RIGHT
//
// It runs on a phone that is propped against a monitor, probably not being
// looked at. So it has to survive: the screen dimming, the browser throttling
// a backgrounded tab, Wi-Fi dropping for a few seconds, and the laptop being
// closed and reopened. Each of those is handled explicitly below rather than
// left to chance, because a camera that silently stops ten minutes in is worse
// than one that never started.

import CameraClient from './CameraClient';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Camera · VisionWorks',
  robots: { index: false, follow: false },
  // The phone should treat this as an app surface, not a document: no pinch
  // zoom fighting the video, and the viewport tight to the screen.
  viewport: {
    width: 'device-width',
    initialScale: 1,
    maximumScale: 1,
    userScalable: false,
  },
};

export default async function PhoneCameraPage({ params }) {
  const { code } = await params;
  return <CameraClient code={String(code || '').toUpperCase()} />;
}
