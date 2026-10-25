import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { PreviewView } from "./preview-view";

export default async function PortalPartnerTargetPreview({
  params,
}: PageProps<"/portal/systems/[systemId]/partners/[targetId]">) {
  const { systemId, targetId } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <PreviewView systemId={systemId} targetId={targetId} />
    </Suspense>
  );
}
