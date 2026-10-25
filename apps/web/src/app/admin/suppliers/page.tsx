import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { SuppliersView } from "./suppliers-view";

export default function SuppliersPage() {
  return (
    <Suspense fallback={<LoadingRows />}>
      <SuppliersView />
    </Suspense>
  );
}
