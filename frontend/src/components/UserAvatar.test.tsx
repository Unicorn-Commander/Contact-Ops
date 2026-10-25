import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { HTMLAttributes, ImgHTMLAttributes, ReactNode } from "react";

import { UserAvatar } from "@/components/UserAvatar";

// Radix waits for the browser image loader before mounting AvatarImage. The
// component test only needs to verify that our profile-picture prop reaches the
// image element; the browser loader itself is covered by Radix.
vi.mock("@/components/ui/avatar", () => ({
  Avatar: ({ children, ...props }: HTMLAttributes<HTMLSpanElement> & { children?: ReactNode }) => (
    <span {...props}>{children}</span>
  ),
  AvatarImage: (props: ImgHTMLAttributes<HTMLImageElement>) => <img {...props} />,
  AvatarFallback: ({ children, ...props }: HTMLAttributes<HTMLSpanElement> & { children?: ReactNode }) => (
    <span {...props}>{children}</span>
  )
}));

describe("UserAvatar", () => {
  it("renders the profile picture when the OIDC profile has a picture claim", () => {
    render(<UserAvatar name="Ada Lovelace" picture="https://profile.example/ada.jpg" />);

    expect(screen.getByRole("img", { name: "Ada Lovelace avatar" })).toHaveAttribute(
      "src",
      "https://profile.example/ada.jpg"
    );
  });

  it("falls back to initials without a picture claim", () => {
    render(<UserAvatar name="Ada Lovelace" />);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("AL")).toBeInTheDocument();
  });
});
