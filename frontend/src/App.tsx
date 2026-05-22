import { RouterProvider, type AnyRouter } from "@tanstack/react-router";

export default function App({ router }: { router: AnyRouter }) {
  return <RouterProvider router={router} />;
}
