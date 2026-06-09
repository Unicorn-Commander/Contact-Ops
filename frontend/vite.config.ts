import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src")
    },
    // Force a SINGLE copy of three + the d3-force-3d layout. `three` and
    // `d3-force-3d` are direct deps AND deps of react-force-graph-3d; without
    // dedupe the bundle can end up with two instances, so the force layout the
    // 3D graph creates isn't the one its animation loop reads →
    // "Cannot read properties of undefined (reading 'tick')" → blank 3D graph.
    dedupe: ["three", "d3-force-3d", "react", "react-dom"]
  },
  server: {
    port: 5173,
    strictPort: false
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          // Heavyweight runtime that's not needed on the auth gate
          motion: ["framer-motion"],
          virtual: ["@tanstack/react-virtual", "react-resizable-panels"],
          cmdk: ["cmdk"],
          // Radix is universally used; keep together
          radix: [
            "@radix-ui/react-avatar",
            "@radix-ui/react-dialog",
            "@radix-ui/react-dropdown-menu",
            "@radix-ui/react-select",
            "@radix-ui/react-switch",
            "@radix-ui/react-tabs",
            "@radix-ui/react-toast",
            "@radix-ui/react-label"
          ]
        }
      }
    }
  }
});
