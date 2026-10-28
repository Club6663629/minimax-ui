import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import HibtvLayout from "./hibtv/HibtvLayout";
import ArtPage from "./hibtv/pages/ArtPage";
import AssetsPage from "./hibtv/pages/AssetsPage";
import BoardPage from "./hibtv/pages/BoardPage";
import CastPage from "./hibtv/pages/CastPage";
import ChangesPage from "./hibtv/pages/ChangesPage";
import HibtvHomePage from "./hibtv/pages/HomePage";
import OutlinePage from "./hibtv/pages/OutlinePage";
import OverviewPage from "./hibtv/pages/OverviewPage";
import ScriptPage from "./hibtv/pages/ScriptPage";
import SettingsPage from "./hibtv/pages/SettingsPage";
import StagingPage from "./hibtv/pages/StagingPage";
import VideoPage from "./hibtv/pages/VideoPage";
import { HibtvProvider } from "./hibtv/state";
import AdminPage from "./pages/AdminPage";
import AuthPage from "./pages/AuthPage";
import CreatePage from "./pages/CreatePage";
import LedgerPage from "./pages/LedgerPage";
import { AuthProvider, useAuth } from "./state/auth";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    // 用暗金底而不是白底，避免未登录时先闪一帧「白屏 + 加载中」再跳登录页
    return (
      <div
        style={{
          minHeight: "100vh",
          background: "#0a0908",
          color: "#948b7e",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 12,
          letterSpacing: ".14em",
        }}
      >
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
            <Route path="/recharge" element={<Navigate to="/ledger" replace />} />
            <Route path="/ledger" element={<LedgerPage />} />
            <Route path="/admin" element={<AdminPage />} />
          </Route>
          <Route
            path="/hibtv"
            element={
              <RequireAuth>
                <HibtvProvider>
                  <HibtvLayout />
                </HibtvProvider>
              </RequireAuth>
            }
          >
            <Route index element={<Navigate to="/hibtv/home" replace />} />
            <Route path="home" element={<HibtvHomePage />} />
            <Route path="overview" element={<OverviewPage />} />
            <Route path="outline" element={<Navigate to="/hibtv/outline/inner" replace />} />
            <Route path="outline/:tab" element={<OutlinePage />} />
            <Route path="cast" element={<CastPage />} />
            <Route path="art" element={<ArtPage />} />
            <Route path="script" element={<Navigate to="/hibtv/script/1" replace />} />
            <Route path="script/:ep" element={<ScriptPage />} />
            <Route path="staging" element={<Navigate to="/hibtv/staging/1" replace />} />
            <Route path="staging/:ep" element={<StagingPage />} />
            <Route path="storyboard" element={<BoardPage />} />
            <Route path="storyboard/:ep" element={<BoardPage />} />
            <Route path="storyboard/:ep/:seg" element={<BoardPage />} />
            <Route path="video/:ep" element={<VideoPage />} />
            <Route path="changes" element={<ChangesPage />} />
            <Route path="assets" element={<AssetsPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
