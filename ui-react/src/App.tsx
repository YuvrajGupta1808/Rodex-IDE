import { Show, SignInButton, SignUpButton, UserButton } from "@clerk/react";

const RODEX_APP_URL = "http://localhost:8000/";

export default function App() {
  return (
    <main className="auth-page">
      <section className="landing-logo">
        <div className="landing-logo-icon">⬡</div>
        <h1>Rodex</h1>
        <p>Security • Bug Detection • Autonomous Fixes • Real-time Streaming</p>
      </section>

      <section className="auth-shell">
        <h2>Authentication</h2>
        <p>Sign in to continue.</p>
        <header className="auth-row">
          <Show when="signed-out">
            <SignInButton mode="modal">
              <button className="auth-btn auth-btn-primary" type="button">
                Sign In
              </button>
            </SignInButton>
            <SignUpButton mode="modal">
              <button className="auth-btn auth-btn-secondary" type="button">
                Sign Up
              </button>
            </SignUpButton>
          </Show>
          <Show when="signed-in">
            <UserButton afterSignOutUrl={RODEX_APP_URL} />
            <a className="auth-btn auth-btn-primary" href={RODEX_APP_URL}>
              Open Rodex
            </a>
          </Show>
        </header>
      </section>
    </main>
  );
}
