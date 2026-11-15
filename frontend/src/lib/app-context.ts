import { createContext, useContext } from "react";

import type { StreamState } from "@/hooks/useStream";
import type { Role } from "@/lib/origin";

export interface AppContextValue {
  role: Role;
  stream: StreamState;
  /** Open the evidence dialog (the exact request behind one evidence item). */
  openEvidence: (id: string) => void;
}

export const AppContext = createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error("useApp must be used inside the app shell.");
  return value;
}
