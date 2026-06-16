import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { NavProvider, useNav } from "./nav";
import Artists from "./pages/Artists";
import Following from "./pages/Following";
import Ignored from "./pages/Ignored";
import Upcoming from "./pages/Upcoming";
import Discover from "./pages/Discover";
import Settings from "./pages/Settings";
import ArtistDetail from "./pages/ArtistDetail";
import AlbumDetail from "./pages/AlbumDetail";

function HomeRedirect() {
  const nav = useNav();
  return <Navigate to={nav.home_path} replace />;
}

export default function App() {
  return (
    <NavProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<HomeRedirect />} />
          <Route path="/artists" element={<Artists />} />
          <Route path="/subscriptions" element={<Following />} />
          <Route path="/upcoming" element={<Upcoming />} />
          <Route path="/discover" element={<Discover />} />
          <Route path="/ignored" element={<Ignored />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/artist/:id" element={<ArtistDetail />} />
          <Route path="/album" element={<AlbumDetail />} />
          <Route path="*" element={<HomeRedirect />} />
        </Route>
      </Routes>
    </NavProvider>
  );
}
