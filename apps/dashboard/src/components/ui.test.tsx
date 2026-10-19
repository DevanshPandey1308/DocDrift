import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Button, EmptyState, StatusBadge } from "./ui";

describe("shared dashboard components", () => {
  it("keeps status meaning in readable text as well as visual treatment", () => {
    render(<StatusBadge value="awaiting_analysis" />);
    expect(screen.getByText("awaiting analysis")).toHaveClass("status-badge");
  });

  it("renders an actionable empty state without implying data exists", () => {
    render(<EmptyState title="No runs" description="Runs will appear when received." />);
    expect(screen.getByRole("heading", { name: "No runs" })).toBeInTheDocument();
    expect(screen.getByText("Runs will appear when received.")).toBeInTheDocument();
  });

  it("exposes button variants as normal keyboard-operable buttons", () => {
    const onClick = vi.fn();
    render(<Button variant="primary" onClick={onClick}>Review</Button>);
    const button = screen.getByRole("button", { name: "Review" });
    button.focus();
    expect(button).toHaveFocus();
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });
});
