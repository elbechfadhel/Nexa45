import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { ExportsView } from "./exports-view";

export default function PortalExports() {
  return (
    <Suspense fallback={<LoadingRows />}>
      <ExportsView />
    </Suspense>
  );
}
