/** @type {import('next').NextConfig} */
const nextConfig = {
  outputFileTracingRoot: import.meta.dirname,
  // Both routes are client components that fetch the API at runtime, so there
  // is no server to deploy — `out/` is a folder of files any static host serves.
  output: "export",
  // Emits out/ops/index.html rather than out/ops.html, so /ops resolves on a
  // plain static host without an extension-guessing rewrite rule.
  trailingSlash: true,
};

export default nextConfig;
