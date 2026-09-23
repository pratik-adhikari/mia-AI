"use client";

import { SignIn } from "@clerk/nextjs";
import Link from "next/link";

const clerkAppearance = {
  variables: {
    colorPrimary: "#0B5FD0",
    colorBackground: "#FFFFFF",
    colorText: "#17181A",
    colorTextSecondary: "#6E6E73",
    colorInputBackground: "#F5F5F7",
    colorInputText: "#17181A",
    colorNeutral: "#17181A",
    borderRadius: "0.75rem",
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
    fontSize: "15px",
  },
  elements: {
    card: "shadow-none p-0 bg-transparent gap-6",
    headerTitle: "text-[22px] font-semibold tracking-tight text-ink",
    headerSubtitle: "text-[14px] text-muted",
    socialButtonsBlockButton:
      "border border-hairline bg-paper hover:bg-mist text-ink text-[14px] font-medium rounded-xl h-11 transition-colors",
    socialButtonsBlockButtonText: "font-medium",
    dividerLine: "bg-hairline",
    dividerText: "text-muted text-[12px]",
    formFieldLabel: "text-[13px] font-medium text-ink",
    formFieldInput:
      "bg-mist border-hairline text-ink placeholder:text-muted rounded-xl h-11 text-[14px] focus:border-signal focus:ring-signal",
    formButtonPrimary:
      "bg-signal hover:bg-signal/90 text-white rounded-xl h-11 text-[14px] font-medium transition-opacity shadow-none",
    footerActionLink: "text-signal hover:text-signal/80 font-medium",
    footerActionText: "text-muted text-[13px]",
    identityPreviewText: "text-ink",
    identityPreviewEditButton: "text-signal",
    alertText: "text-[13px]",
    formFieldErrorText: "text-[12px]",
    logoBox: "hidden",
    rootBox: "w-full",
  },
};

export default function LoginPage() {
  return (
    <div className="flex min-h-screen">
      {/* ── Left panel ── */}
      <div className="relative hidden overflow-hidden bg-ink lg:flex lg:w-[58%] lg:flex-col">
        {/* Animated blobs */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{ zIndex: 0 }}
        >
          <div className="blob blob-1" />
          <div className="blob blob-2" />
          <div className="blob blob-3" />
          {/* Grid overlay */}
          <div
            className="absolute inset-0"
            style={{
              backgroundImage:
                "linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)",
              backgroundSize: "60px 60px",
            }}
          />
        </div>

        {/* Logo */}
        <div className="relative z-10 flex items-center gap-2.5 p-10">
          <Link href="/" className="flex items-center gap-2.5">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-white/10 backdrop-blur-sm ring-1 ring-white/20">
              <span className="h-2 w-2 rounded-[2px] bg-white" />
            </span>
            <span className="text-[15px] font-semibold text-white">MIA</span>
          </Link>
        </div>

        {/* Center content */}
        <div className="relative z-10 flex flex-1 flex-col justify-center px-14 pb-16">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-white/40">
            IDTA 02006 · EU ESPR · AAS JSON
          </p>
          <h1 className="mt-5 text-[40px] font-semibold leading-[1.08] tracking-tight text-white xl:text-[48px]">
            Digital Product Passports
            <br />
            <span className="text-white/50">without the enterprise price.</span>
          </h1>
          <p className="mt-5 max-w-sm text-[16px] leading-relaxed text-white/50">
            Describe your product. MIA maps it to the standard, flags every gap,
            and stops wherever it needs a human decision.
          </p>

          {/* Trust pills */}
          <div className="mt-10 flex flex-wrap gap-2">
            {[
              "IDTA Digital Nameplate",
              "ESPR compliant",
              "Approval gates",
              "Integration Graph",
            ].map((label) => (
              <span
                key={label}
                className="rounded-full border border-white/10 bg-white/5 px-3.5 py-1.5 text-[12px] text-white/50 backdrop-blur-sm"
              >
                {label}
              </span>
            ))}
          </div>
        </div>

        {/* Bottom quote */}
        <div className="relative z-10 border-t border-white/8 px-14 py-8">
          <p className="max-w-sm text-[13px] leading-relaxed text-white/35">
            &ldquo;Connects our SAP export directly to the standard. First
            passport took 20 minutes.&rdquo;
          </p>
          <p className="mt-2 text-[12px] text-white/25">
            Procurement lead, Bavarian mid-size manufacturer
          </p>
        </div>
      </div>

      {/* ── Right panel ── */}
      <div className="flex flex-1 flex-col bg-paper">
        {/* Mobile logo */}
        <div className="flex items-center gap-2 p-6 lg:hidden">
          <Link href="/" className="flex items-center gap-2">
            <span className="grid h-6 w-6 place-items-center rounded-md bg-ink">
              <span className="h-1.5 w-1.5 rounded-[1px] bg-white" />
            </span>
            <span className="text-[15px] font-semibold">MIA</span>
          </Link>
        </div>

        {/* Form area */}
        <div className="flex flex-1 flex-col items-center justify-center px-6 py-12">
          <div className="w-full max-w-[400px]">
            <SignIn appearance={clerkAppearance} />
          </div>
        </div>

        {/* Bottom link */}
        <div className="border-t border-hairline px-6 py-5 text-center">
          <p className="text-[13px] text-muted">
            Don&apos;t have an account?{" "}
            <Link
              href="/signup"
              className="font-medium text-signal hover:text-signal/80 transition-colors"
            >
              Sign up free
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
