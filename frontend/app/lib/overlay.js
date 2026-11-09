// frontend/app/lib/overlay.js
//
// The detection overlay: bounding boxes, posture colours, track IDs and the
// zone outlines beneath them.
//
// WHY THIS IS NOT INSIDE VideoCanvasPlayer ANY MORE
//
// The backend sends a CLEAN jpeg plus a `tracked_entities` list — it does not
// burn boxes into the pixels. Every surface that shows a live frame therefore
// has to draw the overlay itself, and the phone-camera panel did not, so its
// feed was a bare video while the pipeline was working perfectly. That is an
// easy mistake to repeat, and a second copy of this code would be an easy one
// to let drift. One implementation, imported by both.

const POSTURE_COLORS = {
  SITTING:  '#E05A52',
  STANDING: '#5B87D6',
  WALKING:  '#B0821F',
  UNKNOWN:  '#8B8792',
};

import { zoneTypeColor, zoneCanvasLabel } from './zoneTypes';
import { describePpe } from './ppe';

/* A zone's colour comes from its type, from the same module the ZoneEditor
   uses: a zone must be the same colour where it is drawn and where it is
   watched, or the two screens look like they are showing different things. */

/** The space zone polygons are stored in — see ZoneEditor's FRAME_W. */
const ZONE_FRAME_W = 640;

/**
 * The zones this camera is measuring, outlined on the live frame.
 *
 * `zones` was accepted by drawHUD and never used: the overlay drew people and
 * nothing else, so a zone you had just spent time drawing was invisible
 * exactly where it mattered — over the footage it applies to. Without it there
 * is no way to see that a restricted-area polygon is in the wrong place until the
 * time comes out wrong days later.
 *
 * SCALED, NOT DRAWN RAW
 *
 * Polygons are stored in the 640-wide space the CV pipeline works in, and the
 * player's canvas is whatever size the processed frame arrives at (960 wide in
 * practice). Drawing the stored coordinates directly would put every zone at
 * two-thirds of its true position — visibly wrong, and wrong in a way that
 * looks like a tracking fault rather than a scaling one.
 *
 * Drawn FIRST, underneath the detection boxes, and kept faint. This is
 * context, not the subject: a zone outline that competes with a person's
 * bounding box makes the thing you are actually watching harder to read.
 */
function drawZones(ctx, canvas, zones) {
  if (!Array.isArray(zones) || zones.length === 0) return;

  const scale = canvas.width / ZONE_FRAME_W;

  zones.forEach((zone, index) => {
    const pts = zone.polygon_coordinates;
    if (!Array.isArray(pts) || pts.length < 3) return;

    const color = zoneTypeColor(zone.zone_type);

    ctx.beginPath();
    ctx.moveTo(pts[0][0] * scale, pts[0][1] * scale);
    pts.slice(1).forEach(([x, y]) => ctx.lineTo(x * scale, y * scale));
    ctx.closePath();

    ctx.fillStyle = `${color}1A`;
    ctx.fill();
    // Dark under-stroke first (solid), then the coloured dashes on top, so the
    // pale types stay visible over bright footage.
    ctx.strokeStyle = 'rgba(11,10,12,0.55)';
    ctx.lineWidth = 3.5;
    ctx.stroke();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 4]);
    ctx.stroke();
    ctx.setLineDash([]);

    // The type badge and name, at the polygon's centroid rather than its first
    // point: a label pinned to a corner drifts off the shape on an L-shaped zone.
    const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length * scale;
    const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length * scale;
    const label = zoneCanvasLabel(zone, `Zone ${index + 1}`);

    ctx.font = '600 13px Inter, sans-serif';
    const tw = ctx.measureText(label).width;
    ctx.fillStyle = 'rgba(11,10,12,0.72)';
    ctx.fillRect(cx - tw / 2 - 6, cy - 11, tw + 12, 22);
    ctx.fillStyle = color;
    ctx.fillText(label, cx - tw / 2, cy + 5);
  });
}

export function drawHUD(ctx, canvas, entities, zones, enableBlur) {
  // Zones first, so the person boxes sit on top of them.
  drawZones(ctx, canvas, zones);

  // Then person annotations (bounding boxes, track IDs, posture).
  entities.forEach((entity) => {
    const [x1, y1, x2, y2] = entity.bbox;
    const w = x2 - x1;
    const h = y2 - y1;
    // A person with an open alert is outlined in the product red, thicker, and tagged in words.
    const flagged = entity.flagged === true;
    const color = flagged ? '#DC2626' : (POSTURE_COLORS[entity.posture] || POSTURE_COLORS.UNKNOWN);

    // Main box
    ctx.strokeStyle = color;
    ctx.lineWidth = flagged ? 4.5 : 2.5;
    ctx.strokeRect(x1, y1, w, h);

    // Corner reticles
    const r = 12;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x1, y1 + r); ctx.lineTo(x1, y1); ctx.lineTo(x1 + r, y1);
    ctx.moveTo(x2 - r, y1); ctx.lineTo(x2, y1); ctx.lineTo(x2, y1 + r);
    ctx.moveTo(x1, y2 - r); ctx.lineTo(x1, y2); ctx.lineTo(x1 + r, y2);
    ctx.moveTo(x2 - r, y2); ctx.lineTo(x2, y2); ctx.lineTo(x2, y2 - r);
    ctx.stroke();

    // Tag banner (top)
    // Sized to be read at the size the canvas is shown, not at its pixel size.
    ctx.font = '600 14px Inter, sans-serif';
    // "Worker 8", the same name the panels use (not "ID #8"): nobody is named, but one word for one person.
    const tag = `Worker ${entity.track_id}  ${entity.posture}${flagged ? '  FLAGGED' : ''}`;
    const bannerW = Math.max(ctx.measureText(tag).width + 14, w);
    ctx.fillStyle = 'rgba(11,10,12,0.88)';
    ctx.fillRect(x1, y1 - 30, bannerW, 26);
    ctx.fillStyle = '#FFFFFF';
    ctx.fillText(tag, x1 + 7, y1 - 11);

    if (entity.ppe) drawPpeBadge(ctx, canvas, entity.ppe, x1, y2);
  });
}

/**
 * The helmet / vest verdict, as a pill under the person's box.
 *
 * Told apart by words as well as colour (white = OK, red = something not seen,
 * grey = cannot tell), so it reads the same in greyscale and for anyone who
 * cannot tell the colours apart. Kept inside the product's red / white / grey.
 */
function drawPpeBadge(ctx, canvas, ppe, x, bottom) {
  const { label, tone } = describePpe(ppe);
  const fill = tone === 'ok' ? '#FFFFFF' : tone === 'alert' ? '#DC2626' : '#4B4850';
  const ink = tone === 'ok' ? '#0B0A0C' : '#FFFFFF';

  ctx.font = '700 14px Inter, sans-serif';
  const w = ctx.measureText(label).width + 16;
  // Under the box, unless that would fall off the bottom of the frame.
  const y = bottom + 4 + 24 > canvas.height ? bottom - 28 : bottom + 4;
  // Keep the whole pill on the canvas when the person is at the right-hand edge.
  x = Math.max(2, Math.min(x, canvas.width - w - 2));
  ctx.fillStyle = fill;
  ctx.fillRect(x, y, w, 24);
  ctx.strokeStyle = 'rgba(11,10,12,0.55)';
  ctx.lineWidth = 1;
  ctx.strokeRect(x, y, w, 24);
  ctx.fillStyle = ink;
  ctx.fillText(label, x + 8, y + 17);
}
