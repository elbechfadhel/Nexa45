"use client";

/** Image d'un produit ou d'une variante du catalogue fournisseur, servie par l'API (lue à la demande dans le
 *  système du fournisseur). Sans image, ou si elle ne se charge pas : un emplacement neutre. */
import { ImageOff } from "lucide-react";
import { useState } from "react";

import { productImageUrl } from "@/lib/api/supplier-hooks";

export function Thumb({
  productId,
  hasImage,
  alt,
  variantId,
  size = "thumb",
  className = "size-12",
}: {
  productId: string;
  hasImage: boolean;
  alt: string;
  variantId?: string;
  size?: "thumb" | "large";
  className?: string;
}) {
  const [broken, setBroken] = useState(false);
  if (!hasImage || broken) {
    return (
      <div className={`${className} flex items-center justify-center rounded-lg border border-border bg-muted/60`}>
        <ImageOff className="size-4 text-muted-foreground" aria-label="No image" />
      </div>
    );
  }
  return (
    // images servies par l'API (cookie de session, même origine) : pas d'optimisation next/image côté serveur
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={productImageUrl(productId, size, variantId)}
      alt={alt}
      loading="lazy"
      onError={() => setBroken(true)}
      className={`${className} rounded-lg border border-border bg-muted/60 object-contain`}
    />
  );
}
