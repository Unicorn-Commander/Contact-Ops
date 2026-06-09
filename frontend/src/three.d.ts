// `three` ships no bundled type declarations, and we deliberately avoid adding
// the heavy `@types/three` devDependency: it would force a package-lock change
// and a full `npm ci` on every remote (bigboy) build. `lib/graph/avatarNode.ts`
// is the only direct three importer and uses a tiny, stable surface, so we
// declare exactly that here (real types, not `any`). If three usage grows, swap
// this for @types/three.
declare module "three" {
  export class Texture {
    anisotropy: number;
  }
  export class CanvasTexture extends Texture {
    constructor(canvas: HTMLCanvasElement | HTMLImageElement);
  }
  export class SpriteMaterial {
    constructor(parameters?: { map?: Texture; depthWrite?: boolean; transparent?: boolean });
  }
  export class Object3D {
    frustumCulled: boolean;
    scale: { set(x: number, y: number, z: number): void };
  }
  export class Sprite extends Object3D {
    constructor(material?: SpriteMaterial);
  }
}
