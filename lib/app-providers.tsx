"use client";

import { ClerkProvider, useAuth } from "@clerk/nextjs";
import { createContext, useCallback, useContext } from "react";

type AuthProvidersProps = {
  authenticationEnabled: boolean;
  children: React.ReactNode;
};

type TokenGetter = () => Promise<string | null>;

const AuthenticationContext = createContext(true);
const TokenContext = createContext<TokenGetter>(async () => null);

function ClerkTokenProvider({ children }: { children: React.ReactNode }) {
  const { getToken } = useAuth();
  const getSessionToken = useCallback(() => getToken(), [getToken]);
  return <TokenContext.Provider value={getSessionToken}>{children}</TokenContext.Provider>;
}

export function AppProviders({ authenticationEnabled, children }: AuthProvidersProps) {
  const content = authenticationEnabled ? (
    <ClerkProvider afterSignOutUrl="/">
      <ClerkTokenProvider>{children}</ClerkTokenProvider>
    </ClerkProvider>
  ) : (
    <TokenContext.Provider value={async () => null}>{children}</TokenContext.Provider>
  );

  return (
    <AuthenticationContext.Provider value={authenticationEnabled}>
      {content}
    </AuthenticationContext.Provider>
  );
}

export function useAuthenticationEnabled() {
  return useContext(AuthenticationContext);
}

export function useSessionToken() {
  return useContext(TokenContext);
}
