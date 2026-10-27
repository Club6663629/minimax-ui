import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AdminPage from "./pages/AdminPage";
import AuthPage from "./pages/AuthPage";
import CreatePage from "./pages/CreatePage";
import LedgerPage from "./pages/LedgerPage";
import StudioEpisodePage from "./pages/StudioEpisodePage";
import StudioProjectPage from "./pages/StudioProjectPage";
import StudioProjectsPage from "./pages/StudioProjectsPage";
import { AuthProvider, useAuth } from "./state/auth";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-zinc-500">
        加载中…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<AuthPage />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route path="/" element={<CreatePage />} />
            <Route path="/studio" element={<StudioProjectsPage />} />
            <Route path="/studio/:pid" element={<StudioProjectPage />} />
            <Route path="/studio/:pid/ep/:ep" element={<StudioEpisodePage />} />
            <Route path="/recharge" element={<Navigate to="/ledger" replace />} />
            <Route path="/ledger" element={<LedgerPage />} />
            <Route path="/admin" element={<AdminPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
