import { SignIn } from "@clerk/nextjs";

export default function LoginPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-mist px-6 py-12">
      <SignIn />
    </main>
  );
}
