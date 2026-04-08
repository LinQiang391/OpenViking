import { defineConfig } from "tsup";

export default defineConfig({
  entry: { install: "src/index.ts" },
  outDir: "dist",
  format: "esm",
  target: "node22",
  platform: "node",
  splitting: false,
  clean: true,
  minify: false,
  banner: {
    js: "#!/usr/bin/env node",
  },
  noExternal: ["@clack/prompts", "citty", "picocolors"],
});
