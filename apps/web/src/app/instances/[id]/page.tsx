import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { InstanceView } from "./instance-view";

export default async function InstancePage({ params }: PageProps<"/instances/[id]">) {
  const { id } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <InstanceView id={id} />
    </Suspense>
  );
}
