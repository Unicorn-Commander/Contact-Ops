import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GraphCanvas } from "./GraphCanvas";
import type { EgoGraph } from "@/hooks/useEgoGraph";

const fits = vi.hoisted(() => ({ two: vi.fn(), three: vi.fn() }));
vi.mock("react-force-graph-2d", async () => {
  const React = await import("react");
  return { default: React.forwardRef((props: any, ref) => {
    React.useImperativeHandle(ref, () => ({ zoomToFit: fits.two }));
    return <div data-testid="renderer-2d" data-width={props.width} data-height={props.height} />;
  }) };
});
vi.mock("react-force-graph-3d", async () => {
  const React = await import("react");
  return { default: React.forwardRef((props: any, ref) => {
    React.useImperativeHandle(ref, () => ({ zoomToFit: fits.three, controls: () => ({}) }));
    return <div data-testid="renderer-3d" data-width={props.width} data-height={props.height} />;
  }) };
});

const graph: EgoGraph = {
  nodes: [{ id: "person", name: "Synthetic person", kind: "Person", group: "Person" }],
  links: [], node_count: 1, edge_count: 0, truncated: false
};
const props = { graph, selectedNodeId: null, renderMode: "2d" as const, onNodeClick: vi.fn() };
let measured = { width: 390, height: 780 };
const observers: FakeObserver[] = [];
class FakeObserver {
  callback: ResizeObserverCallback;
  element: Element | null = null;
  disconnected = false;
  constructor(callback: ResizeObserverCallback) { this.callback = callback; observers.push(this); }
  observe(element: Element) { this.element = element; }
  disconnect() { this.disconnected = true; }
  emit() { this.callback([{ contentRect: measured } as ResizeObserverEntry], this as unknown as ResizeObserver); }
}

beforeEach(() => {
  measured = { width: 390, height: 780 }; observers.length = 0; vi.clearAllMocks();
  vi.stubGlobal("ResizeObserver", FakeObserver);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => ({ ...measured, x: 0, y: 0, top: 0, left: 0, right: measured.width, bottom: measured.height, toJSON: () => ({}) }));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("graph viewport lifecycle", () => {
  it("measures the real canvas when async loading finishes, including mode remount", async () => {
    const view = render(<GraphCanvas {...props} loading />);
    expect(observers).toHaveLength(0);
    view.rerender(<GraphCanvas {...props} />);
    expect(screen.getByTestId("renderer-2d")).toHaveAttribute("data-width", "390");
    expect(screen.getByTestId("renderer-2d")).toHaveAttribute("data-height", "780");
    expect(observers.at(-1)?.element).not.toBeNull();
    const old = observers.at(-1)!;
    view.rerender(<GraphCanvas {...props} loading />);
    expect(old.disconnected).toBe(true);
    measured = { width: 1024, height: 700 };
    view.rerender(<GraphCanvas {...props} renderMode="3d" />);
    await waitFor(() => expect(screen.getByTestId("renderer-3d")).toHaveAttribute("data-width", "1024"));
    expect(observers.at(-1)?.disconnected).toBe(false);
    view.unmount(); expect(observers.at(-1)?.disconnected).toBe(true);
  });

  it("fits a resized viewport but preserves a user's camera until explicit recenter", () => {
    const view = render(<GraphCanvas {...props} />);
    fits.two.mockClear();
    measured = { width: 800, height: 600 };
    act(() => observers.at(-1)!.emit());
    expect(screen.getByTestId("renderer-2d")).toHaveAttribute("data-width", "800");
    expect(fits.two).toHaveBeenCalled();
    fireEvent.wheel(observers.at(-1)!.element!);
    fits.two.mockClear();
    measured = { width: 390, height: 780 };
    act(() => observers.at(-1)!.emit());
    expect(screen.getByTestId("renderer-2d")).toHaveAttribute("data-width", "390");
    expect(fits.two).not.toHaveBeenCalled();
    view.rerender(<GraphCanvas {...props} fitNonce={1} />);
    expect(fits.two).toHaveBeenCalledWith(700, 50);
  });
});
