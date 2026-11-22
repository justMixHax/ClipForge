import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Empty } from "./components/ui";
import Clipper from "./pages/Clipper";
import DashboardPage from "./pages/Dashboard";
import Editor, { EditorIndex } from "./pages/Editor";
import LibraryPage from "./pages/Library";
import Planned from "./pages/Planned";
import SettingsPage from "./pages/Settings";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/clipper" element={<Clipper />} />
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/editor" element={<EditorIndex />} />
        <Route path="/editor/:clipId" element={<Editor />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/discovery" element={<Planned area="discovery" />} />
        <Route path="/publishing" element={<Planned area="publishing" />} />
        <Route path="/analytics" element={<Planned area="analytics" />} />
        <Route path="/automation" element={<Planned area="automation" />} />
        <Route path="/strategist" element={<Planned area="strategist" />} />
        <Route path="*" element={<Empty title="That page does not exist">Use the menu on the left.</Empty>} />
      </Routes>
    </Layout>
  );
}
