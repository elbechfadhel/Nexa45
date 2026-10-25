import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { CatalogView } from "./catalog-view";

export default function PortalCatalog() {
  return (
    <Suspense fallback={<LoadingRows />}>
      <CatalogView />
    </Suspense>
  );
}
