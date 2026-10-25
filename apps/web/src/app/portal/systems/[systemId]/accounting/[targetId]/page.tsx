import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { ApiTargetView } from "./api-target-view";

export default async function PortalAccountingApiTarget({
  params,
}: PageProps<"/portal/systems/[systemId]/accounting/[targetId]">) {
  const { systemId, targetId } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <ApiTargetView systemId={systemId} targetId={targetId} />
    </Suspense>
  );
}
