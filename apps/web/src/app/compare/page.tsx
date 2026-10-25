import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { CompareView } from "./compare-view";

export default function ComparePage() {
  return (
    <Suspense fallback={<LoadingRows />}>
      <CompareView />
    </Suspense>
  );
}
