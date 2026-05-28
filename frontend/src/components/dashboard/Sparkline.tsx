/**
 * Tiny inline SVG sparkline — a single smoothed area + line, no axes, no deps.
 *
 * Sits beside a stat number to give a 7-point trend at a glance. Stroke +
 * fill inherit `currentColor` so the caller tints it with the tile's semantic
 * accent. Purely decorative: marked aria-hidden (the trend delta carries the
 * accessible meaning in text).
 */
import { useMemo } from "react";
import { cn } from "@/lib/utils";

export interface SparklineProps {
  /** Series of values, oldest → newest. 2+ points render a line; <2 renders nothing. */
  data: number[];
  width?: number;
  height?: number;
  /** Stroke width of the trend line. */
  strokeWidth?: number;
  className?: string;
}

export function Sparkline({ data, width = 64, height = 22, strokeWidth = 1.5, className }: SparklineProps) {
  const geometry = useMemo(() => {
    if (data.length < 2) return null;
    const min = Math.min(...data);
    const max = Math.max(...data);
    const span = max - min || 1;
    const stepX = width / (data.length - 1);
    // Inset vertically by half the stroke so the line never clips at the edges.
    const pad = strokeWidth;
    const usableH = height - pad * 2;

    const points = data.map((value, index) => {
      const x = index * stepX;
      const y = pad + usableH - ((value - min) / span) * usableH;
      return [Number(x.toFixed(2)), Number(y.toFixed(2))] as const;
    });

    const line = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x},${y}`).join(" ");
    const area = `${line} L${width},${height} L0,${height} Z`;
    return { line, area, last: points[points.length - 1] };
  }, [data, width, height, strokeWidth]);

  if (!geometry) return null;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("overflow-visible", className)}
      fill="none"
      aria-hidden="true"
      role="presentation"
    >
      <path d={geometry.area} fill="currentColor" fillOpacity={0.12} />
      <path
        d={geometry.line}
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={geometry.last[0]} cy={geometry.last[1]} r={strokeWidth + 0.5} fill="currentColor" />
    </svg>
  );
}
