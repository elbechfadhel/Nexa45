import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { LoginView } from "./login-view";

export default function LoginPage() {
  return (
    <Suspense fallback={<LoadingRows />}>
      <LoginView />
    </Suspense>
  );
}
