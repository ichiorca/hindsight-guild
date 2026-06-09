import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { ThemeProvider } from "@/lib/theme";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Toaster } from "@/components/ui/Toaster";
import Queue from "@/routes/Queue";
import WeeklyReview from "@/routes/WeeklyReview";
import Drafting from "@/routes/Drafting";
import Experiments from "@/routes/Experiments";
import Skills from "@/routes/Skills";
import Voice from "@/routes/Voice";
import Telemetry from "@/routes/Telemetry";
import Live from "@/routes/Live";
import Capabilities from "@/routes/Capabilities";
import Agents from "@/routes/Agents";
import Signals from "@/routes/Signals";
import Learning from "@/routes/Learning";
import Published from "@/routes/Published";
import Admin from "@/routes/Admin";

export default function App() {
  return (
    <ThemeProvider>
      <ErrorBoundary>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/queue" replace />} />
          <Route path="/queue"         element={<Queue />} />
          <Route path="/published"     element={<Published />} />
          <Route path="/weekly-review" element={<WeeklyReview />} />
          <Route path="/draft"         element={<Drafting />} />
          <Route path="/signals"       element={<Signals />} />
          <Route path="/learning"      element={<Learning />} />
          <Route path="/experiments"   element={<Experiments />} />
          <Route path="/skills"        element={<Skills />} />
          <Route path="/voice"         element={<Voice />} />
          <Route path="/telemetry"     element={<Telemetry />} />
          <Route path="/capabilities"  element={<Capabilities />} />
          <Route path="/agents"        element={<Agents />} />
          <Route path="/live"          element={<Live />} />
          <Route path="/admin"         element={<Admin />} />
          <Route path="*" element={<Navigate to="/queue" replace />} />
        </Route>
      </Routes>
      <Toaster />
      </ErrorBoundary>
    </ThemeProvider>
  );
}
