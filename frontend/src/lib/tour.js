import { driver } from 'driver.js';
import 'driver.js/dist/driver.css';

/** The 60-second first-run tour of the Explore screen. */
export function startTour() {
  const steps = [
    { element: '#tour-presets', popover: { title: 'Start with a known object', description: 'These five are the challenge test targets. Barnard’s Star is the easiest to watch move.' } },
    { element: '#tour-coords', popover: { title: 'Or point the telescope anywhere', description: 'Type a sky position in hours/degrees or plain decimals, or press Random sky position to pick one for you.' } },
    { element: '#tour-stage', popover: { title: 'Watch the sky change', description: 'Each frame is a SPHEREx snapshot of the same patch, months apart. Anything that jumps while the background stays still is moving.' } },
    { element: '#tour-timeline', popover: { title: 'Snapshots over time', description: 'Play, pause or step through. Untick a snapshot to leave it out.' } },
    { element: '#tour-modes', popover: { title: 'Compare images', description: 'Blink flips between snapshots. Difference makes fixed stars vanish. Stack builds one deeper image.' } },
    { element: '#tour-expert', popover: { title: 'Ready for more?', description: 'Expert mode adds bands, spectra, brightness charts, catalogue overlays and data-quality flags.' } },
    { element: '#tour-ask', popover: { title: 'Stuck? Ask', description: 'The assistant explains whatever is on screen in plain language.' } },
  ].filter((s) => document.querySelector(s.element));
  driver({ showProgress: true, popoverClass: 'starxplorer-tour', steps, nextBtnText: 'Next', prevBtnText: 'Back', doneBtnText: 'Done' }).drive();
}
