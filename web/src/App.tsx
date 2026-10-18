import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AdminPage from "./pages/AdminPage";
import AuthPage from "./pages/AuthPage";
import CreatePage from "./pages/CreatePage";
import DramaListPage from "./pages/DramaListPage";
import DramaProjectPage from "./pages/DramaProjectPage";
import DramaWorkspacePage from "./pages/DramaWorkspacePage";
import LedgerPage from "./pages/LedgerPage";
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
            <Route path="/drama" element={<DramaListPage />} />
            <Route path="/drama/:pid" element={<DramaProjectPage />} />
            <Route path="/drama/:pid/ep/:eid" element={<DramaWorkspacePage />} />
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
