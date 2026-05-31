/**
 * AmbientGraph — Contact-Ops's signature object, rendered as an ambient,
 * glowing relationship graph on a single <canvas>.
 *
 * This is the centerpiece for v2 brand surfaces (login hero, dashboard hero,
 * empty states). It's deliberately lightweight: a 2D canvas node/edge field
 * with gentle drift + bloom, NOT WebGL. One rAF loop, no React re-renders per
 * frame, node count capped, DPR-aware. Drawing uses only fill/stroke with
 * additive-ish radial gradients for glow — cheap and smooth.
 *
 * Performance:
 *   - nodeCount is capped (default 28, max enforced at 60).
 *   - The loop pauses when the canvas scrolls out of view (IntersectionObserver)
 *     and when the tab is hidden (visibilitychange).
 *   - prefers-reduced-motion → draw ONE static frame and never animate.
 *
 * Accessibility: purely decorative, aria-hidden. All foreground text/content is
 * layered above it by the caller and carries its own AA contrast.
 */
import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

export interface AmbientGraphProps {
  /** Number of nodes. Clamped to [8, 60]. */
  nodeCount?: number;
  /** Connection distance as a fraction of the canvas diagonal. */
  linkDistance?: number;
  /** Drift speed multiplier. */
  speed?: number;
  /** Density of the dim particle dust behind the graph. */
  dust?: number;
  className?: string;
}

type Node = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  hueFuchsia: boolean;
  pulse: number;
};

type Dust = { x: number; y: number; r: number; a: number };

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Read an OKLCH triple from a CSS var on the host element, with a fallback. */
function readOklch(el: HTMLElement, varName: string, fallback: string): string {
  const v = getComputedStyle(el).getPropertyValue(varName).trim();
  return v || fallback;
}

/** Read a numeric CSS var (an alpha multiplier), with a fallback. */
function readNum(el: HTMLElement, varName: string, fallback: number): number {
  const v = parseFloat(getComputedStyle(el).getPropertyValue(varName));
  return Number.isFinite(v) ? v : fallback;
}

export function AmbientGraph({
  nodeCount = 28,
  linkDistance = 0.22,
  speed = 1,
  dust = 1,
  className
}: AmbientGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvasEl = canvasRef.current;
    if (!canvasEl) return;
    const context2d = canvasEl.getContext("2d");
    if (!context2d) return;
    // Bind to non-null consts so TS keeps the narrowing inside the rAF closures
    // (narrowing on the nullable refs is otherwise lost across function boundaries).
    const canvas: HTMLCanvasElement = canvasEl;
    const ctx: CanvasRenderingContext2D = context2d;

    const reduce = prefersReducedMotion();
    const count = Math.max(8, Math.min(60, Math.round(nodeCount)));

    // Colors are read once from the v2 tokens on the nearest themed ancestor so
    // the graph follows the showcase's dark/light toggle.
    const host = canvas.parentElement ?? canvas;
    let nodeColor = readOklch(host, "--co-v2-node", "0.74 0.16 304");
    let nodeFuchsia = readOklch(host, "--co-v2-node-fuchsia", "0.74 0.2 330");
    let edgeColor = readOklch(host, "--co-v2-edge", "0.6 0.14 304");
    // Per-theme canvas alpha multipliers (dark mode is brighter so the signature
    // graph reads with equal presence in both themes). Defaults reproduce the
    // original look if the tokens are ever absent.
    let coreAlpha = readNum(host, "--co-v2-node-core-alpha", 0.85);
    let haloAlpha = readNum(host, "--co-v2-node-halo-alpha", 0.35);
    let edgeAlpha = readNum(host, "--co-v2-edge-alpha", 0.32);
    let dustAlpha = readNum(host, "--co-v2-dust-alpha", 1);

    let width = 0;
    let height = 0;
    let dpr = Math.min(window.devicePixelRatio || 1, 2);
    let nodes: Node[] = [];
    let dustField: Dust[] = [];
    let raf = 0;
    let running = false;
    let lastT = 0;

    const rand = (min: number, max: number) => min + Math.random() * (max - min);

    function seed() {
      nodes = Array.from({ length: count }, () => ({
        x: rand(0, width),
        y: rand(0, height),
        vx: rand(-0.16, 0.16) * speed,
        vy: rand(-0.16, 0.16) * speed,
        r: rand(1.6, 3.4),
        hueFuchsia: Math.random() < 0.28,
        pulse: rand(0, Math.PI * 2)
      }));
      const dustCount = Math.round((width * height) / 14000 * dust);
      dustField = Array.from({ length: Math.min(dustCount, 140) }, () => ({
        x: rand(0, width),
        y: rand(0, height),
        r: rand(0.4, 1.3),
        a: rand(0.04, 0.16)
      }));
    }

    // Pull the theme-dependent palette + canvas alphas off the v2 tokens. Called
    // on mount, on resize, and whenever the document theme flips so the graph's
    // dark-mode luminosity boost applies live (the dark theme sets brighter
    // node/edge hues + higher alphas than light — see tokens-v2.css).
    function readThemeColors() {
      nodeColor = readOklch(host, "--co-v2-node", "0.74 0.16 304");
      nodeFuchsia = readOklch(host, "--co-v2-node-fuchsia", "0.74 0.2 330");
      edgeColor = readOklch(host, "--co-v2-edge", "0.6 0.14 304");
      coreAlpha = readNum(host, "--co-v2-node-core-alpha", 0.85);
      haloAlpha = readNum(host, "--co-v2-node-halo-alpha", 0.35);
      edgeAlpha = readNum(host, "--co-v2-edge-alpha", 0.32);
      dustAlpha = readNum(host, "--co-v2-dust-alpha", 1);
    }

    function resize() {
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      readThemeColors();
      seed();
    }

    function draw(now: number) {
      const dt = lastT ? Math.min((now - lastT) / 16.67, 3) : 1;
      lastT = now;

      ctx.clearRect(0, 0, width, height);

      // Dim particle dust behind the graph for depth.
      for (const d of dustField) {
        ctx.beginPath();
        ctx.fillStyle = `oklch(${nodeColor} / ${Math.min(1, d.a * dustAlpha).toFixed(3)})`;
        ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        ctx.fill();
      }

      const maxDist = Math.hypot(width, height) * linkDistance;
      const maxDistSq = maxDist * maxDist;

      // Edges — fade with distance. Drawn before nodes so nodes sit on top.
      ctx.lineWidth = 1;
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const distSq = dx * dx + dy * dy;
          if (distSq > maxDistSq) continue;
          const t = 1 - distSq / maxDistSq;
          ctx.beginPath();
          ctx.strokeStyle = `oklch(${edgeColor} / ${Math.min(1, t * edgeAlpha).toFixed(3)})`;
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }

      // Nodes — each with a soft radial bloom halo + bright core.
      for (const n of nodes) {
        if (!reduce) {
          n.x += n.vx * dt;
          n.y += n.vy * dt;
          n.pulse += 0.015 * dt;
          if (n.x < 0 || n.x > width) n.vx *= -1;
          if (n.y < 0 || n.y > height) n.vy *= -1;
          n.x = Math.max(0, Math.min(width, n.x));
          n.y = Math.max(0, Math.min(height, n.y));
        }
        const color = n.hueFuchsia ? nodeFuchsia : nodeColor;
        const pulse = reduce ? 0.7 : 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(n.pulse));
        const haloR = n.r * 6;
        const grad = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, haloR);
        grad.addColorStop(0, `oklch(${color} / ${Math.min(1, haloAlpha * pulse).toFixed(3)})`);
        grad.addColorStop(1, `oklch(${color} / 0)`);
        ctx.beginPath();
        ctx.fillStyle = grad;
        ctx.arc(n.x, n.y, haloR, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.fillStyle = `oklch(${color} / ${Math.min(1, coreAlpha * pulse).toFixed(3)})`;
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fill();
      }

      if (running && !reduce) raf = requestAnimationFrame(draw);
    }

    function start() {
      if (running || reduce) return;
      running = true;
      lastT = 0;
      raf = requestAnimationFrame(draw);
    }
    function stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    }

    resize();
    // Always paint at least one frame (covers reduced-motion + initial state).
    draw(performance.now());

    const ro = new ResizeObserver(() => {
      resize();
      if (reduce) draw(performance.now());
    });
    ro.observe(canvas);

    // Pause when offscreen.
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries.some((e) => e.isIntersecting);
        if (visible) start();
        else stop();
      },
      { threshold: 0.01 }
    );
    io.observe(canvas);

    const onVis = () => {
      if (document.hidden) stop();
      else start();
    };
    document.addEventListener("visibilitychange", onVis);

    // Re-read the palette when the document theme flips (data-theme / class on
    // <html>), so the dark-mode luminosity boost takes effect immediately on a
    // live toggle. Repaint once in case the loop is paused (reduced-motion /
    // offscreen) so the static frame also updates.
    const themeObserver = new MutationObserver(() => {
      readThemeColors();
      if (!running) draw(performance.now());
    });
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class"]
    });

    return () => {
      stop();
      ro.disconnect();
      io.disconnect();
      themeObserver.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [nodeCount, linkDistance, speed, dust]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className={cn("pointer-events-none absolute inset-0 h-full w-full", className)}
    />
  );
}
