"use client";

import { SignUp } from "@clerk/nextjs";
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
    alertText: "text-[13px]",
    formFieldErrorText: "text-[12px]",
    logoBox: "hidden",
    rootBox: "w-full",
  },
};

export default function SignUpPage() {
  return (
    <div className="flex min-h-screen">
      {/* ── Left panel ── */}
      <div className="relative hidden overflow-hidden bg-ink lg:flex lg:w-[58%] lg:flex-col">
        <div aria-hidden className="pointer-events-none absolute inset-0" style={{ zIndex: 0 }}>
          <div className="blob blob-1" />
          <div className="blob blob-2" />
          <div className="blob blob-3" />
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
            Free to start · No card required
          </p>
          <h1 className="mt-5 text-[40px] font-semibold leading-[1.08] tracking-tight text-white xl:text-[48px]">
            Your first passport
            <br />
            <span className="text-white/50">in under 20 minutes.</span>
          </h1>
          <p className="mt-5 max-w-sm text-[16px] leading-relaxed text-white/50">
            Create your account and start mapping product data to IDTA standards
            immediately. No setup, no enterprise contract.
          </p>

          {/* Steps */}
          <div className="mt-10 space-y-4">
            {[
              { n: "01", label: "Describe your product or paste a URL" },
              { n: "02", label: "Review AI-proposed field mappings" },
              { n: "03", label: "Download your AAS-compliant passport" },
            ].map((step) => (
              <div key={step.n} className="flex items-center gap-4">
                <span className="font-mono text-[11px] text-white/30">{step.n}</span>
                <span className="text-[14px] text-white/50">{step.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="relative z-10 border-t border-white/8 px-14 py-8">
          <p className="text-[12px] text-white/25">
            Built for EU ESPR compliance · IDTA 02006 · Asset Administration Shell
          </p>
        </div>
      </div>

      {/* ── Right panel ── */}
      <div className="flex flex-1 flex-col bg-paper">
        <div className="flex items-center gap-2 p-6 lg:hidden">
          <Link href="/" className="flex items-center gap-2">
            <span className="grid h-6 w-6 place-items-center rounded-md bg-ink">
              <span className="h-1.5 w-1.5 rounded-[1px] bg-white" />
            </span>
            <span className="text-[15px] font-semibold">MIA</span>
          </Link>
        </div>

        <div className="flex flex-1 flex-col items-center justify-center px-6 py-12">
          <div className="w-full max-w-[400px]">
            <SignUp appearance={clerkAppearance} />
          </div>
        </div>

        <div className="border-t border-hairline px-6 py-5 text-center">
          <p className="text-[13px] text-muted">
            Already have an account?{" "}
            <Link
              href="/login"
              className="font-medium text-signal hover:text-signal/80 transition-colors"
            >
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
