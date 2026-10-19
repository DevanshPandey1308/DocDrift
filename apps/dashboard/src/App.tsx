import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { ApiError, getSession, logout } from "./api/client";
import { AppShell, SignInPage } from "./components/AppShell";
import { ErrorPanel, LoadingState } from "./components/ui";
import { Overview } from "./screens/Overview";
import { Repositories, RepositoryPage } from "./screens/Repositories";
import { RunPage } from "./screens/RunPage";
import { SettingsPage } from "./screens/SettingsPage";

export default function App() {
  const session = useQuery({ queryKey: ["session"], queryFn: getSession, retry: false });
  const queryClient = useQueryClient();
  const signOut = useMutation({
    mutationFn: logout,
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["session"] }),
  });

  if (session.isPending) return <main className="full-page-state"><LoadingState label="Checking GitHub session…" /></main>;
  if (session.isError && session.error instanceof ApiError && session.error.status === 401) return <SignInPage />;
  if (session.isError) return <main className="full-page-state"><ErrorPanel error={session.error} /></main>;

  return (
    <AppShell
      login={session.data.github_login}
      onSignOut={() => signOut.mutate()}
      signOutPending={signOut.isPending}
      signOutError={signOut.isError ? signOut.error.message : undefined}
    >
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/repositories" element={<Repositories />} />
        <Route path="/repositories/:repositoryId" element={<RepositoryPage />} />
        <Route path="/runs/:runId" element={<RunPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<p className="route-not-found">The requested dashboard page does not exist.</p>} />
      </Routes>
    </AppShell>
  );
}
