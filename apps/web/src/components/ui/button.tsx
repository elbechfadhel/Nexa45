import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "cn"

// Charte Transliate : classes du pack (design-pack/transliate.css, .btn / .btn-*). API shadcn inchangée.
// Primaire = encre (#0f172a), y compris pour les actions de création : aucun bouton orange (choix du 2026-10-16).
// Les utilitaires ci-dessous ne gardent que le comportement : la forme vient du pack.
const buttonVariants = cva(
  "btn group/button shrink-0 whitespace-nowrap bg-clip-padding select-none disabled:pointer-events-none aria-invalid:ring-2 aria-invalid:ring-destructive [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "btn-primary",
        outline: "btn-secondary aria-expanded:bg-muted",
        secondary: "btn-secondary",
        ghost: "btn-ghost aria-expanded:bg-muted",
        destructive: "btn-danger",
        link: "bg-transparent p-0 text-brand underline-offset-4 hover:underline",
      },
      size: {
        default: "",
        xs: "btn-sm h-6 gap-1 px-2 [&_svg:not([class*='size-'])]:size-3",
        sm: "btn-sm gap-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "btn-lg",
        icon: "size-9 p-0",
        "icon-xs": "size-6 p-0 [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-8 p-0",
        "icon-lg": "size-10 p-0",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
