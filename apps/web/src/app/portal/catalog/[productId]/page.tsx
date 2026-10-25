import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { ProductView } from "./product-view";

export default async function PortalProduct({ params }: PageProps<"/portal/catalog/[productId]">) {
  const { productId } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <ProductView productId={productId} />
    </Suspense>
  );
}
