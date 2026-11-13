/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ['lucide-react', 'recharts'],
  // The launcher's production mode (run_system.py --prod) builds into its own folder, so a production
  // build and a development server never share (and overwrite) one `.next`.
  distDir: process.env.NEXT_DIST_DIR || '.next',
};

export default nextConfig;
