import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// Authentication UI stays public; all durable user data lives behind these routes.
const isProtectedRoute = createRouteMatcher([
  "/workspace(.*)",
  "/products(.*)",
  "/research(.*)",
]);

export default clerkMiddleware(async (auth, request) => {
  if (isProtectedRoute(request)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    "/((?!_next|.well-known/workflow|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/:path*",
  ],
};
