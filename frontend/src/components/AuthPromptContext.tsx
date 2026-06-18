import { createContext, useContext } from "react";

interface AuthPromptContextValue {
  openLoginPrompt: () => void;
  requireSignIn: () => boolean;
  signedIn: boolean;
}

const defaultAuthPromptContext: AuthPromptContextValue = {
  openLoginPrompt: () => undefined,
  requireSignIn: () => true,
  signedIn: true
};

export const AuthPromptContext = createContext<AuthPromptContextValue>(defaultAuthPromptContext);

export function useAuthPrompt(): AuthPromptContextValue {
  return useContext(AuthPromptContext);
}
