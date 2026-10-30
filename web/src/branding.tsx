import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api } from "./api";

interface Branding {
  logo_text: string;
  organization_name: string;
  workspace_description: string;
}
const fallback: Branding = {
  logo_text: "LLM",
  organization_name: "LLM Router",
  workspace_description: "",
};
const Context = createContext({
  branding: fallback,
  updateBranding: (_value: Branding) => {},
});
export const useBranding = () => useContext(Context);

export function BrandingProvider({ children }: { children: ReactNode }) {
  const [branding, updateBranding] = useState(fallback);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    void api<Branding>("/api/branding")
      .then(updateBranding)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    document.title = `${branding.organization_name} — LLM Router`;
  }, [branding.organization_name]);
  return (
    <Context.Provider value={{ branding, updateBranding }}>
      {loading ? null : children}
    </Context.Provider>
  );
}

export function BrandMark() {
  const { branding } = useBranding();
  const parts = branding.logo_text.match(/^(.*?)(\d+)$/);
  return (
    <span
      className="brand-symbol"
      title={branding.organization_name}
      style={{ fontSize: branding.logo_text.length > 5 ? "20px" : undefined }}
    >
      {parts ? (
        <>
          {parts[1]}
          <span>{parts[2]}</span>
        </>
      ) : (
        branding.logo_text
      )}
    </span>
  );
}
