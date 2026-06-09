/**
 * Avatar/logo node rendering for the relationship graph — the "people graph"
 * look. People render as circular initials pucks and organizations as rounded-
 * square tiles, both coloured by the SAME deterministic hue as the directory
 * cards (`@/lib/avatar`), so an entity is instantly recognizable in the list and
 * in the graph. Two renderers share one visual language:
 *   - 2D: `drawAvatarNode2d` paints directly into the ForceGraph2D canvas with
 *     zoom-aware level-of-detail (initials only appear once the puck is big
 *     enough on screen).
 *
 * (3D nodes use ForceGraph3D's default coloured spheres — see GraphCanvas. The
 * old custom CanvasTexture sprite was removed: it both blanked the 3D scene and
 * pulled a second `three` instance into the static bundle, which broke
 * react-force-graph-3d's force layout. `three` now lives ONLY in the dynamic 3D
 * chunk.)
 *
 * No remote photos are loaded yet (the graph node payload carries no image URL),
 * so the monogram puck IS the avatar — which keeps the graph fast, sovereign
 * (no third-party image egress), and visually consistent with the rest of the app.
 */
import { avatarColor } from "@/lib/avatar";
import { initials } from "@/lib/utils";

type GraphNodeLike = {
  id: string;
  name: string;
  kind: string;
  val?: number;
  x?: number;
  y?: number;
};

function isOrgKind(kind: string): boolean {
  const k = kind.toLowerCase();
  return k === "organization" || k === "org" || k === "company";
}

/** Node radius in graph units, derived from the node's `val` (importance). */
function nodeRadius(node: GraphNodeLike): number {
  return Math.max(3, Math.sqrt(node.val ?? 4) * 3.2);
}

function roundRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  rad: number
): void {
  ctx.moveTo(x + rad, y);
  ctx.arcTo(x + w, y, x + w, y + h, rad);
  ctx.arcTo(x + w, y + h, x, y + h, rad);
  ctx.arcTo(x, y + h, x, y, rad);
  ctx.arcTo(x, y, x + w, y, rad);
}

export type AvatarDrawOptions = {
  highlighted: boolean;
  /** Resolved ring colour (canvas-ready) for resting nodes. */
  ringColor: string;
  /** Resolved ring colour for the selected/hovered/incident set. */
  ringColorActive: string;
};

/**
 * Paint one node as an avatar puck into the 2D force-graph canvas. Used as
 * `nodeCanvasObject` with `nodeCanvasObjectMode="replace"`.
 */
export function drawAvatarNode2d(
  node: GraphNodeLike,
  ctx: CanvasRenderingContext2D,
  globalScale: number,
  opts: AvatarDrawOptions
): void {
  const x = node.x ?? 0;
  const y = node.y ?? 0;
  const org = isOrgKind(node.kind);
  const r = nodeRadius(node) * (opts.highlighted ? 1.25 : 1);

  ctx.save();
  // Puck body.
  ctx.beginPath();
  if (org) roundRectPath(ctx, x - r, y - r, r * 2, r * 2, r * 0.34);
  else ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = avatarColor(node.name || node.id);
  ctx.fill();
  // Ring — brand-tinted when the node is in focus, soft otherwise.
  ctx.lineWidth = (opts.highlighted ? 2 : 1.1) / globalScale;
  ctx.strokeStyle = opts.highlighted ? opts.ringColorActive : opts.ringColor;
  ctx.stroke();
  // Initials — level-of-detail: only when the puck is large enough on screen to
  // be legible, so a zoomed-out overview reads as calm coloured dots.
  if (r * globalScale > 7) {
    const text = initials(node.name) || "?";
    ctx.fillStyle = "white";
    ctx.font = `600 ${r * 0.84}px Inter, ui-sans-serif, system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x, y + r * 0.04);
  }
  ctx.restore();
}

/**
 * Paint the node's clickable/hover hit-area for the 2D graph. Used as
 * `nodePointerAreaPaint` so hit-testing matches the custom puck shape.
 */
export function paintAvatarPointerArea2d(
  node: GraphNodeLike,
  color: string,
  ctx: CanvasRenderingContext2D
): void {
  const x = node.x ?? 0;
  const y = node.y ?? 0;
  const r = nodeRadius(node);
  ctx.beginPath();
  if (isOrgKind(node.kind)) roundRectPath(ctx, x - r, y - r, r * 2, r * 2, r * 0.34);
  else ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}
