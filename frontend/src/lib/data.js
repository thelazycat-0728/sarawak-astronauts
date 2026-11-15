// Static content: detector arrays, observation epochs, presets, glossary,
// lessons, game levels, ranks and the demo review queue.
import { parseRA, parseDec, mulberry32 } from './util';

// SPHEREx detector arrays (microns), from the IRSA SPHEREx documentation.
export const ARRAYS = [
  { n: 1, lo: 0.75, hi: 1.09 },
  { n: 2, lo: 1.10, hi: 1.62 },
  { n: 3, lo: 1.63, hi: 2.41 },
  { n: 4, lo: 2.42, hi: 3.82 },
  { n: 5, lo: 3.83, hi: 4.41 },
  { n: 6, lo: 4.42, hi: 5.00 },
];
export const CHANNELS_PER_ARRAY = 17;

export const DAY0 = Date.UTC(2025, 4, 24);
const epoch = (label, date, release) => ({
  label, date, release, t: (Date.parse(`${date}T00:00:00Z`) - DAY0) / (365.25 * 864e5),
});

/** One snapshot per six-month all-sky survey. */
export const SURVEYS = [
  epoch('Survey 1', '2025-05-24', 'DR1'),
  epoch('Survey 2', '2025-11-19', 'DR1'),
  epoch('Survey 3', '2026-05-17', 'QR3'),
  epoch('Survey 4', '2026-11-11', 'QR3'),
];

/** Near the ecliptic poles SPHEREx looks almost every month. */
export const MONTHLY = Array.from({ length: 18 }, (_, i) => {
  const d = new Date(Date.UTC(2025, 4 + i, 15));
  const iso = d.toISOString().slice(0, 10);
  return epoch(`Visit ${i + 1}`, iso, d < new Date(Date.UTC(2026, 4, 16)) ? 'DR1' : 'QR3');
});

// ---------------------------------------------------------------- presets
// flux = peak brightness per detector array (simulation units, noise = 1).
// slug = folder under public/presets/ holding real SPHEREx frames for this target;
// realArray = the one detector array those frames cover (index.json "band"). The
// default array is that one, so a preset opens on real data.
const P = [
  {
    id: 'barnard', name: "Barnard's Star", type: 'High proper motion star',
    raStr: '17h57m48.50s', decStr: '+04°41′36.2″',
    kind: 'motion', pm: [-801.6, 10362.4], pmText: '10.4″/yr',
    flux: [60, 75, 70, 45, 34, 28], spectrum: 'm-dwarf', epochs: SURVEYS, defaultArray: 2, seed: 101,
    slug: 'barnards-star', realArray: 2,
    lesson: { kicker: 'Movement lesson', title: 'What changed position?', text: 'Watch the bright point travel while the background stars stay fixed.' },
    facts: [
      'A red dwarf about 6 light-years away, one of the Sun’s closest neighbours.',
      'It has the largest proper motion of any known star: about 10.4″ a year.',
      'In SPHEREx’s 6.15″ pixels that is roughly 1.7 pixels a year.',
    ],
  },
  {
    id: 'epsindib', name: 'Epsilon Indi B', type: 'Brown dwarf pair',
    raStr: '22h04m10.52s', decStr: '−56°46′57.7″',
    kind: 'motion', pm: [3966.7, -2536.2], pmText: '4.7″/yr',
    flux: [6, 14, 16, 15, 20, 22], spectrum: 't-dwarf', epochs: SURVEYS, defaultArray: 2, seed: 202,
    slug: 'eps-indi-b', realArray: 2,
    lesson: { kicker: 'Subtle motion lesson', title: 'Too small to see? Subtract.', text: 'It moves less than a pixel a year. Switch to Difference and look for a teal–amber pair.' },
    facts: [
      'Two brown dwarfs orbiting the star Epsilon Indi A, about 12 light-years away.',
      'Too close together to separate at SPHEREx resolution, so they appear as one source.',
      'They move about 4.7″ a year, under one SPHEREx pixel.',
    ],
  },
  {
    id: 'wise0855', name: 'WISE 0855', type: 'Fast-moving brown dwarf',
    raStr: '08h55m10.831s', decStr: '−07°14′42.53″',
    kind: 'motion', pm: [-8118, 680], pmText: '8.1″/yr',
    flux: [0, 0, 0.8, 9, 20, 30], spectrum: 'y-dwarf', epochs: SURVEYS, defaultArray: 6, seed: 303,
    slug: 'wise-0855', realArray: 6,
    lesson: { kicker: 'Colour lesson', title: 'Where did it go?', text: 'This brown dwarf is too cold to glow at short wavelengths. Compare array 2 with array 6.' },
    facts: [
      'About 7.4 light-years away and one of the coldest brown dwarfs known, roughly 250 K.',
      'It moves about 8.1″ a year, almost entirely east to west.',
      'Too faint for Gaia: only infrared surveys like WISE and SPHEREx can track it.',
    ],
  },
  {
    id: 'rwaur', name: 'RW Aurigae', type: 'Variable young star',
    raStr: '05h07m49.54s', decStr: '+30°24′05.1″',
    kind: 'variable', pm: [0, 0], lc: [1, 0.42, 0.88, 0.58],
    flux: [40, 50, 55, 60, 58, 55], spectrum: 'yso', epochs: SURVEYS, defaultArray: 2, seed: 404,
    slug: 'rw-aur', realArray: 2,
    lesson: { kicker: 'Brightness lesson', title: 'Which star faded?', text: 'Nothing moves here. Blink Survey 1 against Survey 2 and find the star that dims.' },
    facts: [
      'A young star system in Auriga, still surrounded by gas and dust.',
      'It has gone through deep, irregular dimming episodes, likely as dusty material passed in front of it.',
      'SPHEREx sees each dip in many infrared colours at once.',
    ],
  },
  {
    id: 'rdra', name: 'R Draconis', type: 'Mira variable star',
    raStr: '16h32m40.17s', decStr: '+66°45′17.8″',
    kind: 'variable', pm: [0, 0], period: 245, amp: 0.55,
    flux: [30, 48, 55, 50, 44, 40], spectrum: 'mira', epochs: MONTHLY, defaultArray: 2, seed: 505,
    slug: 'r-dra', realArray: 2,
    lesson: { kicker: 'Brightness lesson', title: 'Watch it pulse', text: 'Near the ecliptic pole SPHEREx looks every month. Press play and watch one star breathe in and out.' },
    facts: [
      'A Mira variable: a cool giant star that swells and shrinks with a period of about 245 days.',
      'Fields near the ecliptic poles are observed almost continuously.',
      'Monthly snapshots turn into a full light curve.',
    ],
  },
];
export const PRESETS = P.map((p) => ({ ...p, ra: parseRA(p.raStr), dec: parseDec(p.decStr) }));
export const PRESET_BY_ID = Object.fromEntries(PRESETS.map((p) => [p.id, p]));

/** A typed-in field. About half of them hide a simulated "mystery mover" to find and submit. */
export function customObject(seed) {
  const rng = mulberry32(seed ^ 0x5eed);
  if (rng() < 0.45) return null;
  const speed = 900 + rng() * 2600;
  const ang = rng() * Math.PI * 2;
  const bright = 7 + rng() * 10;
  return {
    id: 'mystery', mystery: true, name: 'Unidentified source', kind: 'motion',
    pm: [Math.cos(ang) * speed, Math.sin(ang) * speed],
    flux: [0.3, 0.6, 0.8, 1.0, 1.15, 1.25].map((k) => bright * k),
    spectrum: rng() < 0.5 ? 't-dwarf' : 'm-dwarf',
  };
}

// ---------------------------------------------------------------- glossary
// Shared with the backend's AI explainer (backend/app/ai/glossary.json is a copy;
// a backend test checks the two match). Edit frontend/src/glossary.json, then copy it.
export { default as GLOSSARY } from '../glossary.json';

// ---------------------------------------------------------------- lessons
export const LESSONS = [
  { id: 'move', title: 'Watch Barnard’s Star move', minutes: 2, preset: 'barnard', mode: 'blink', skill: 'Spotting motion',
    steps: ['Press play and watch the four snapshots.', 'Find the bright point that drifts upward.', 'Open Difference to see the teal–amber pair.'] },
  { id: 'colour', title: 'Find a star that hides in infrared', minutes: 3, preset: 'wise0855', mode: 'blink', array: 2, skill: 'Using bands',
    steps: ['Start in array 2: nothing special is there.', 'Switch to array 6.', 'A new object appears and slides sideways.'] },
  { id: 'subtract', title: 'Catch a one-pixel shift', minutes: 3, preset: 'epsindib', mode: 'diff', skill: 'Difference images',
    steps: ['Difference subtracts the first snapshot.', 'Fixed stars vanish into the background.', 'Look for a faint teal–amber pair.'] },
  { id: 'brightness', title: 'See R Draconis brighten and fade', minutes: 2, preset: 'rdra', mode: 'blink', skill: 'Variability',
    steps: ['Press play: 18 monthly snapshots.', 'One star swells and fades over about 8 months.', 'In Expert mode, open its brightness chart.'] },
];

// ---------------------------------------------------------------- game levels
export const LEVELS = [
  { id: 'barnard', preset: 'barnard', arrays: [3], difficulty: 1, tol: 2.4, cosmic: [4], seed: 11,
    mission: 'The fastest-moving star in the sky is in this field. Play the flipbook and click the star that drifts between surveys.',
    hint: 'It is one of the brighter stars and creeps north (up). Blink Survey 1 against Survey 4.' },
  { id: 'wise0855', preset: 'wise0855', arrays: [2, 6], defaultArray: 2, difficulty: 2, tol: 2.4, cosmic: [], seed: 23,
    mission: 'A brown dwarf colder than a freezer is crossing this field. You won’t find it by looking harder, only by looking in a different colour.',
    hint: 'Objects this cold barely glow below 2 µm. Switch to array 6, then watch for something sliding sideways.' },
  { id: 'epsindib', preset: 'epsindib', arrays: [6], difficulty: 3, tol: 2.8, cosmic: [7], seed: 37,
    mission: 'A brown dwarf pair is shifting by about one pixel over the whole mission. The flipbook alone won’t show it.',
    hint: 'Use Difference. Fixed stars cancel; look for a teal and amber pair side by side.' },
  { id: 'rwaur', preset: 'rwaur', arrays: [2], difficulty: 2, tol: 2.6, cosmic: [], seed: 41, change: true,
    mission: 'Nothing here moves. One star fades and recovers. Find the one whose brightness changes.',
    hint: 'Blink Survey 1 against Survey 2. One bright star drops sharply.' },
  { id: 'rdra', preset: 'rdra', arrays: [2], difficulty: 2, tol: 2.6, cosmic: [], seed: 53, change: true,
    mission: 'SPHEREx looks here almost every month. One star is slowly pulsing. Find it.',
    hint: 'Play the flipbook fast. One star swells and fades over about eight months.' },
  { id: 'asteroid', preset: null, arrays: [3], difficulty: 3, tol: 2.6, cosmic: [3, 9], seed: 67,
    object: { id: 'asteroid', name: 'Unknown asteroid', kind: 'asteroid', flux: [20, 24, 26, 26, 28, 30], astEpoch: 2, astStep: [2.6, 1.4], spectrum: 'star' },
    mission: 'Bonus round. Something in our own Solar System crossed this field during one visit only.',
    hint: 'Watch Survey 3 exposure by exposure. Single bright pixels in one frame are cosmic rays, not asteroids.',
    facts: [
      'Asteroids move many pixels within one visit, then vanish from the next survey.',
      'Every candidate is checked against known-asteroid catalogues (SkyBoT).',
      'If nothing known matches, it could be a new find.',
    ] },
];

// ---------------------------------------------------------------- ranks
export const RANKS = [
  { name: 'Cadet', min: 0 },
  { name: 'Spotter', min: 20 },
  { name: 'Observer', min: 60 },
  { name: 'Navigator', min: 150 },
  { name: 'Discoverer', min: 400 },
];
export function rankFor(points = 0) {
  let i = 0;
  RANKS.forEach((r, k) => { if (points >= r.min) i = k; });
  return { ...RANKS[i], index: i, next: RANKS[i + 1] ?? null };
}

// ---------------------------------------------------------------- demo review queue
// rep = the submitter's reputation (0–100, lib/reputation.js), which scales queue priority.
export const DEMO_QUEUE = [
  { id: 'd1', code: 'SX-0142', by: 'nurul.a', rep: 84, agreed: 5, ai: 91, ageH: 3, seed: 9001, kind: 'mover',
    note: 'Faint dot moving south-west in every survey. Not in the Gaia overlay.',
    signals: { motion: 'Consistent across 4 snapshots', snr: 14.2, flags: 'Clear', catalogue: 'No match (Gaia, CatWISE)', asteroid: 'No known asteroid' } },
  { id: 'd2', code: 'SX-0139', by: 'kelvin_t', rep: 61, agreed: 3, ai: 74, ageH: 9, seed: 9002, kind: 'mover',
    note: 'Moves a little each survey, very red. Brighter in array 6.',
    signals: { motion: 'Consistent across 3 snapshots', snr: 7.9, flags: 'Clear', catalogue: 'No match (Gaia, CatWISE)', asteroid: 'No known asteroid' } },
  { id: 'd3', code: 'SX-0131', by: 'aisyah.r', rep: 38, agreed: 2, ai: 38, ageH: 20, seed: 9003, kind: 'flash',
    note: 'Bright spot that appears in only one snapshot.',
    signals: { motion: 'Seen in 1 snapshot only', snr: 11.0, flags: 'TRANSIENT near position', catalogue: 'No match', asteroid: 'No known asteroid' } },
  { id: 'd4', code: 'SX-0127', by: 'danial99', rep: 9, agreed: 1, ai: 22, ageH: 30, seed: 9004, kind: 'mover', hiddenNote: true,
    note: 'contact me at 012-345 6789 for details',
    signals: { motion: 'Inconsistent direction', snr: 4.1, flags: 'HALO nearby', catalogue: 'No match', asteroid: 'No known asteroid' } },
  { id: 'd5', code: 'SX-0120', by: 'mei.ling', rep: 52, agreed: 4, ai: 57, ageH: 44, seed: 9005, kind: 'mover',
    note: 'Possible slow mover near a bright star.',
    signals: { motion: 'Consistent across 4 snapshots', snr: 6.3, flags: 'Clear', catalogue: 'No match (Gaia, CatWISE)', asteroid: 'No known asteroid' } },
];

// ---------------------------------------------------------------- time machine
export const TM_SURVEYS = {
  '2MASS': { color: '#45d3b8', band: 'Ks', wave: 2.2 },
  WISE: { color: '#4ade80', band: 'W1', wave: 3.4 },
  NEOWISE: { color: '#4ade80', band: 'W1', wave: 3.4 },
  SPHEREx: { color: '#7c6cf5', band: 'Array 3', wave: 2.0 },
};
export function timeMachineEpochs() {
  const list = [{ survey: '2MASS', year: 1998.8, n: 1 }, { survey: 'WISE', year: 2010.5, n: 12 }];
  for (let y = 2014.3; y < 2024; y += 1) list.push({ survey: 'NEOWISE', year: +y.toFixed(1), n: 8 });
  [2025.39, 2025.88, 2026.38, 2026.86].forEach((y) => list.push({ survey: 'SPHEREx', year: y, n: 3 }));
  return list.map((e) => ({ ...e, t: e.year - 2025.39, label: e.survey, date: String(Math.floor(e.year)), release: e.survey }));
}
export const TM_TARGETS = [
  { id: 'barnard', fov: 12, brightness: 70, note: 'Fastest-moving star in the sky' },
  { id: 'epsindib', fov: 8, brightness: 22, note: 'Brown dwarf pair, 12 light-years away' },
  { id: 'wise0855', fov: 8, brightness: 26, coldBefore: 2010, note: 'Invisible until WISE launched in 2009' },
];
