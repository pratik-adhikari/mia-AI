import { withWorkflow } from "workflow/next";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // Next's detached TypeScript CLI process loses stdout under Node 24.
    // The compiler API performs the same check and works on Node 20 and newer.
    useTypeScriptCli: false,
  },
};
export default withWorkflow(nextConfig);
