/** @type {import('next').NextConfig} */
const nextConfig = {
  // Pure static export: works on Vercel even when build/root detection is flaky,
  // and matches a "Static Site" style deploy (HTML/CSS/JS only; API stays on Render).
  output: "export",
};

export default nextConfig;
