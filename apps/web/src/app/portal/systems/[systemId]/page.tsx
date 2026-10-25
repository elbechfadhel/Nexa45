import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { SystemView } from "./system-view";

export default async function PortalSystem({ params }: PageProps<"/portal/systems/[systemId]">) {
  const { systemId } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <SystemView systemId={systemId} />
    </Suspense>
  );
}
