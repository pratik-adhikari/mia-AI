import { SignUp } from "@clerk/nextjs";

export default function SignupPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-mist px-6 py-12">
      <SignUp />
    </main>
  );
}
