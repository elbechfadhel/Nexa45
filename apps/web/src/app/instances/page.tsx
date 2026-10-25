import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { InstancesView } from "./instances-view";

export default function InstancesPage() {
  return (
    <Suspense fallback={<LoadingRows />}>
      <InstancesView />
    </Suspense>
  );
}
