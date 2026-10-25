import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { PreviewView } from "./preview-view";

export default async function PortalProductTargetPreview({
  params,
}: PageProps<"/portal/systems/[systemId]/products/[targetId]">) {
  const { systemId, targetId } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <PreviewView systemId={systemId} targetId={targetId} />
    </Suspense>
  );
}
