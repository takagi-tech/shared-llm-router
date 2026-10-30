import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  type FormEvent,
} from "react";
import ReactDOM from "react-dom/client";
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  ArrowRight,
  ChevronRight,
  LogOut,
  Menu,
  Server,
  Settings2,
  Terminal,
  X,
} from "lucide-react";
import { api, mutate, setCSRF, setRefresh } from "./api";
import { ErrorBox, Field, Loading } from "./components";
import { Overview } from "./pages/Overview";
import { Registry, EndpointDetail, Aliases } from "./pages/Registry";
import { Requests } from "./pages/Requests";
import { Users } from "./pages/Users";
import { Benchmarks } from "./pages/Benchmarks";
import { SettingsPage } from "./pages/Settings";
import type { Config, Session } from "./types";
import "./styles.css";
import { BrandMark, BrandingProvider, useBranding } from "./branding";

const NoticeContext = createContext<(message: string) => void>(() => {});
export const useNotice = () => useContext(NoticeContext);

const navigation = [
  { to: "/", label: "モデル確認", english: "Models", icon: Server },
  { to: "/settings", label: "設定", english: "Settings", icon: Settings2 },
];

function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const { branding } = useBranding();
  const [username, setUsername] = useState("admin"),
    [password, setPassword] = useState("");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [initialized, setInitialized] = useState(true);
  useEffect(() => {
    void api<{ initialized: boolean }>("/api/auth/status")
      .then((x) => setInitialized(x.initialized))
      .catch((e) => setError(e.message));
  }, []);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(
        await mutate<Session>("/api/auth/login", "POST", {
          username,
          password,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-layout">
      <aside className="login-aside">
        <Link to="/" className="brand">
          <BrandMark />
          <span>LLM ROUTER</span>
        </Link>
        <div>
          <div className="eyebrow">RESEARCH INFRASTRUCTURE</div>
          <h1>
            One gateway.
            <br />
            Every model.
          </h1>
          <p>{branding.workspace_description}</p>
        </div>
        <span className="login-bottom">
          {branding.organization_name} NETWORK / PRIVATE INFRASTRUCTURE
        </span>
      </aside>
      <main className="login-main">
        <form onSubmit={submit}>
          <div className="eyebrow">ADMIN CONSOLE</div>
          <h2>管理者ログイン</h2>
          <p className="muted">推論環境の状態と利用状況を管理します。</p>
          {!initialized && (
            <div className="error-box">
              管理者が未作成です。起動ホストで <code>llm-router admin</code>{" "}
              を実行してください。
            </div>
          )}
          <Field label="ユーザー名">
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              autoFocus
            />
          </Field>
          <Field label="パスワード">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>
          <ErrorBox message={error} />
          <button
            className="button primary login-submit"
            disabled={busy || !initialized}
          >
            {busy ? "確認中…" : "ログイン"}
            <ArrowRight size={16} />
          </button>
          <div className="login-note">
            <Terminal size={15} />
            <span>推論APIは利用者のAPI Keyで接続できます。</span>
          </div>
        </form>
      </main>
    </div>
  );
}

function Console({
  session,
  onLogout,
}: {
  session: Session;
  onLogout: () => void;
}) {
  const { branding } = useBranding();
  const location = useLocation();
  const [menu, setMenu] = useState(false),
    [notice, setNotice] = useState("");
  const section =
    navigation.find(
      (n) => n.to !== "/" && location.pathname.startsWith(n.to),
    ) || navigation[0];
  useEffect(() => {
    setMenu(false);
  }, [location.pathname]);
  useEffect(() => {
    if (!notice) return;
    const id = setTimeout(() => setNotice(""), 6000);
    return () => clearTimeout(id);
  }, [notice]);
  useEffect(() => {
    void api<Config>("/api/admin/settings")
      .then((c) => setRefresh(c.ui_refresh_seconds))
      .catch(() => {});
  }, []);
  async function logout() {
    try {
      await mutate("/api/auth/logout", "POST");
      onLogout();
    } catch (e) {
      setNotice((e as Error).message);
    }
  }
  return (
    <NoticeContext.Provider value={setNotice}>
      <div className="app-layout">
        <aside className={`sidebar ${menu ? "open" : ""}`}>
          <Link to="/" className="brand">
            <BrandMark />
            <span>LLM ROUTER</span>
          </Link>
          <div className="workspace-label">
            {branding.organization_name} NETWORK
            <span>{branding.workspace_description}</span>
          </div>
          <nav aria-label="メインナビゲーション">
            {navigation.map(({ to, label, icon: Icon }) => (
              <NavLink end={to === "/"} to={to} key={to}>
                <Icon size={17} strokeWidth={1.6} />
                <span>{label}</span>
                <ChevronRight className="nav-chevron" size={12} />
              </NavLink>
            ))}
          </nav>
          <div className="sidebar-footer">
            <span className="environment-mark" />
            <div>
              <strong>PRIVATE WORKSPACE</strong>
              <span>{branding.organization_name} LLM Router · v0.1</span>
            </div>
          </div>
        </aside>
        {menu && (
          <button
            className="mobile-overlay"
            onClick={() => setMenu(false)}
            aria-label="メニューを閉じる"
          />
        )}
        <div className="main-layout">
          <div className="topbar">
            <button
              className="icon-button mobile-menu"
              aria-label="メニュー"
              onClick={() => setMenu(!menu)}
            >
              {menu ? <X size={19} /> : <Menu size={19} />}
            </button>
            <div className="breadcrumbs">
              <span>Console</span>
              <ChevronRight size={12} />
              <span>{section.english}</span>
            </div>
            {!session.open && (
              <div className="topbar-user">
                <span className="admin-avatar">
                  {session.username[0].toUpperCase()}
                </span>
                <span>{session.username}</span>
                <button
                  className="icon-button"
                  title="ログアウト"
                  aria-label="ログアウト"
                  onClick={logout}
                >
                  <LogOut size={16} />
                </button>
              </div>
            )}
          </div>
          <main className="main-content">
            <Routes>
              <Route index element={<Registry />} />
              <Route path="overview" element={<Overview />} />
              <Route path="endpoints" element={<Navigate to="/" replace />} />
              <Route path="endpoints/:id" element={<EndpointDetail />} />
              <Route path="aliases" element={<Aliases />} />
              <Route path="requests" element={<Requests />} />
              <Route path="users" element={<Users />} />
              <Route path="benchmarks" element={<Benchmarks />} />
              <Route path="benchmarks/:id" element={<Benchmarks />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
          <footer className="page-footer">
            <span>{branding.organization_name} / LLM INFRASTRUCTURE</span>
            <span>登録・制御・観測</span>
          </footer>
        </div>
        {notice && (
          <div className="toast" role="status">
            <span>{notice}</span>
            <button
              aria-label="通知を閉じる"
              className="icon-button"
              onClick={() => setNotice("")}
            >
              <X size={15} />
            </button>
          </div>
        )}
      </div>
    </NoticeContext.Provider>
  );
}

function App() {
  const navigate = useNavigate();
  const [session, setSession] = useState<Session | null>(null),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    void api<Session>("/api/auth/session")
      .then((s) => {
        setCSRF(s.csrf);
        setSession(s);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
    const expired = () => {
      setSession(null);
      setCSRF("");
    };
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  if (loading) return <Loading />;
  return (
    <Routes>
      <Route path="/admin/*" element={<StripAdmin />} />
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route
        path="*"
        element={
          session ? (
            <Console
              session={session}
              onLogout={() => {
                setCSRF("");
                setSession(null);
                navigate("/", { replace: true });
              }}
            />
          ) : (
            <Login
              onLogin={(s) => {
                setCSRF(s.csrf);
                setSession(s);
              }}
            />
          )
        }
      />
    </Routes>
  );
}

// Pages used to live under /admin; keep old bookmarks working.
function StripAdmin() {
  const location = useLocation();
  const path = location.pathname.replace(/^\/admin/, "") || "/";
  return <Navigate to={`${path}${location.search}`} replace />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <BrandingProvider>
        <App />
      </BrandingProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
