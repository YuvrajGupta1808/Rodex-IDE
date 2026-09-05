import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth,
  sendSignInLinkToEmail,
  isSignInWithEmailLink,
  signInWithEmailLink,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

const EMAIL_STORAGE_KEY = "rodex_pending_email";

const msgEl = document.getElementById("auth-msg");
const authBox = document.getElementById("auth-box");
const emailInput = document.getElementById("email-input");
const sendBtn = document.getElementById("btn-send-link");

function showMessage(text, kind) {
  msgEl.textContent = text;
  msgEl.className = `auth-msg ${kind || ""}`;
}

async function main() {
  const configRes = await fetch("/auth/config");
  const firebaseConfig = await configRes.json();
  const app = initializeApp(firebaseConfig);
  const auth = getAuth(app);

  if (isSignInWithEmailLink(auth, window.location.href)) {
    authBox.style.display = "none";
    let email = window.localStorage.getItem(EMAIL_STORAGE_KEY);
    if (!email) {
      email = window.prompt("Confirm your email to finish signing in:");
    }
    try {
      const result = await signInWithEmailLink(auth, email, window.location.href);
      const idToken = await result.user.getIdToken();
      const res = await fetch("/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken }),
      });
      if (!res.ok) throw new Error("Session creation failed");
      window.localStorage.removeItem(EMAIL_STORAGE_KEY);
      window.location.replace("/");
    } catch (err) {
      authBox.style.display = "flex";
      showMessage("That link is invalid or expired. Please request a new one.", "error");
    }
    return;
  }

  sendBtn.addEventListener("click", async () => {
    const email = emailInput.value.trim();
    if (!email) {
      showMessage("Enter your email first.", "error");
      return;
    }
    sendBtn.disabled = true;
    showMessage("Sending...", "");
    try {
      const actionCodeSettings = { url: window.location.href, handleCodeInApp: true };
      await sendSignInLinkToEmail(auth, email, actionCodeSettings);
      window.localStorage.setItem(EMAIL_STORAGE_KEY, email);
      showMessage("Check your inbox for a sign-in link.", "ok");
    } catch (err) {
      showMessage(err.message || "Could not send link.", "error");
    } finally {
      sendBtn.disabled = false;
    }
  });
}

main();
