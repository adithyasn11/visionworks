// Loading state for Operations.
// Three summary tiles, then the five triage sections in their real order.
import React from 'react';
import { SkeletonPage, SkeletonHeader, SkeletonTiles, SkeletonCard } from '../components/Skeleton';

export default function Loading() {
  return (
    <SkeletonPage>
      <SkeletonHeader withAction />
      <SkeletonTiles count={3} cols="grid-cols-3" />
      <SkeletonCard rows={2} headerWidth="w-44" />
      <SkeletonCard rows={2} headerWidth="w-56" />
      <SkeletonCard rows={1} headerWidth="w-64" />
      <SkeletonCard rows={1} headerWidth="w-52" />
      <SkeletonCard rows={1} headerWidth="w-32" />
    </SkeletonPage>
  );
}
